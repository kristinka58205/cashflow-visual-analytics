import uuid
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q
from .access import get_scope, write_scope
from .audit import record_event
from .models import BankAccount, CashTransfer


def scoped_transfers(scope):
    accounts = scope.catalog(BankAccount)
    return CashTransfer.objects.filter(from_account__in=accounts, to_account__in=accounts)


class TransferForm(forms.ModelForm):
    class Meta:
        model = CashTransfer
        fields = ["from_account", "to_account", "amount", "occurred_on", "description", "command_key"]
        widgets = {"occurred_on":forms.DateInput(attrs={"type":"date"}), "command_key":forms.HiddenInput}
        labels = {"from_account":"Со счёта", "to_account":"На счёт"}

    def __init__(self, *args, scope, **kwargs):
        super().__init__(*args, **kwargs)
        for key in ["from_account", "to_account"]:
            self.fields[key].queryset = scope.catalog(BankAccount)

    def validate_unique(self):
        # Повтор того же UUID проверяет сервис вместе с точным содержимым.
        pass


def create_transfer(user, scope, **values):
    with write_scope(user, scope) as scope:
        for key in ["from_account", "to_account"]:
            get_object_or_404(scope.catalog(BankAccount), pk=values[key].pk)
        previous = CashTransfer.objects.filter(command_key=values["command_key"]).first()
        if previous:
            same = previous.owner_id == user.pk and scoped_transfers(scope).filter(pk=previous.pk).exists()
            for key in ["from_account", "to_account", "amount", "occurred_on", "description"]:
                same = same and getattr(previous, key) == values[key]
            if not same: raise ValidationError("Ключ перевода уже использован с другими данными.")
            return previous, False
        item = CashTransfer(owner=user, **values)
        item.full_clean();item.save()
        record_event(scope, "created", "transfer", item.pk, f"{item.from_account} → {item.to_account}: {item.amount:.2f} ₽")
        return item, True


@login_required
def transfer_list(request):
    scope = get_scope(request)
    if request.method == "POST" and not scope.can_write: raise PermissionDenied
    form = TransferForm(request.POST if request.method == "POST" else None, scope=scope, initial={"command_key":uuid.uuid4()})
    if request.method == "POST" and form.is_valid():
        try: item, created = create_transfer(request.user, scope, **form.cleaned_data)
        except ValidationError as exc: form.add_error(None, exc)
        else:
            messages.success(request, "Перевод записан." if created else "Этот перевод уже записан.")
            return redirect("transfer_list")
    page = Paginator(scoped_transfers(scope).select_related("from_account", "to_account"), 25).get_page(request.GET.get("page"))
    return render(request, "cashflow/transfers.html", {"form":form, "page":page})
