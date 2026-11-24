from decimal import Decimal
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import PaymentForm, PaymentExecutionForm, RevisionForm
from .models import PlannedPayment, CashTransaction, BankAccount
from .access import LedgerScope, get_scope, financial_write, write_scope
from .audit import record_event
from .revisions import require_revision


@transaction.atomic
def save_payment(user, payment, scope=None, expected_revision=None):
    with write_scope(user, scope) as scope:
        if not scope.catalog(BankAccount).filter(pk=payment.account_id).exists():
            raise PermissionDenied
        if payment.status!='planned' or payment.transaction_id is not None:
            raise ValidationError('Нельзя изменить состояние платежа через редактор плана.')
        if payment.pk:
            current=get_object_or_404(scope.payments().select_for_update(),pk=payment.pk)
            if current.status!='planned':raise ValidationError('Платёж уже проведён или отменён; изменение плана невозможно.')
            require_revision(current,expected_revision)
            payment.revision=current.revision+1
        action = "updated" if payment.pk else "created"
        payment.full_clean()
        payment.save()
        record_event(scope, action, "planned_payment", payment.pk, f"{payment.description}: {payment.amount:.2f} ₽")


@transaction.atomic
def execute_payment(user, pk, occurred_on, scope=None, expected_revision=None):
    with write_scope(user, scope) as scope:
        payment = get_object_or_404(scope.payments().select_for_update(), pk=pk)
        if payment.status == "paid":
            return payment.transaction
        if payment.status != "planned":
            raise ValidationError("Отменённый платёж нельзя провести.")
        require_revision(payment,expected_revision)
        entry = CashTransaction(account=payment.account, category=payment.category, counterparty=payment.counterparty,
            project=payment.project, amount=payment.amount, description=payment.description, occurred_on=occurred_on)
        entry.full_clean()
        entry.save()
        payment.status, payment.transaction = "paid", entry
        payment.revision+=1
        payment.save(update_fields=["status", "transaction", "revision"])
        record_event(scope, "paid", "planned_payment", payment.pk, f"{payment.description}: {payment.amount:.2f} ₽")
        record_event(scope, "created", "operation", entry.pk, f"Плановый платёж: {entry.description}")
        return entry


@login_required
def payment_list(request):
    state = request.GET.get("state", "planned")
    state = state if state in {"planned", "paid", "cancelled", "all"} else "planned"
    entries = get_scope(request).payments().select_related("account", "category")
    totals = {"income": Decimal("0.00"), "expense": Decimal("0.00")}
    for kind, amount in entries.filter(status="planned").values_list("category__kind", "amount"):
        totals[kind] += amount
    if state != "all":
        entries = entries.filter(status=state)
    return render(request, "cashflow/payments.html", {"page": Paginator(entries, 25).get_page(request.GET.get("page")),
        "state": state, "totals": totals, "today": timezone.localdate()})


@login_required
@financial_write
def payment_edit(request, pk=None):
    instance = get_object_or_404(get_scope(request).payments(), pk=pk, status="planned") if pk else None
    form = PaymentForm(request.POST if request.method == "POST" else None, instance=instance, owner=request.user, scope=get_scope(request))
    if request.method == "POST" and form.is_valid():
        try:
            save_payment(request.user, form.save(commit=False), scope=get_scope(request), expected_revision=form.cleaned_data.get("expected_revision"))
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("payment_list")
    return render(request, "cashflow/payment_form.html", {"form": form, "heading": "Плановый платёж"})


@login_required
@financial_write
def payment_execute(request, pk):
    payment = get_object_or_404(get_scope(request).payments(), pk=pk)
    form = PaymentExecutionForm(request.POST if request.method == "POST" else None, initial={"occurred_on": timezone.localdate(),"expected_revision":payment.revision})
    if request.method == "POST" and form.is_valid():
        try:
            execute_payment(request.user, pk, scope=get_scope(request), **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, "Платёж проведён однократно и включён в фактические операции.")
            return redirect("payment_list")
    return render(request, "cashflow/payment_form.html", {"form": form, "heading": "Провести платёж", "payment": payment})


@login_required
@require_POST
@financial_write
@transaction.atomic
def payment_cancel(request, pk):
    payment = get_object_or_404(get_scope(request).payments(), pk=pk)
    form=RevisionForm(request.POST)
    if payment.status=='planned':
        try:
            if not form.is_valid():raise ValidationError('Некорректная версия формы. Обновите список платежей.')
            require_revision(payment,form.cleaned_data['expected_revision'])
        except ValidationError as exc:
            messages.error(request,' '.join(exc.messages))
        else:
            payment.status='cancelled';payment.revision+=1
            payment.save(update_fields=['status','revision'])
            record_event(get_scope(request),'cancelled','planned_payment',payment.pk,payment.description)
    return redirect('payment_list')
