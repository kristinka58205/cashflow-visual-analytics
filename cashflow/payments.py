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
from .forms import PaymentForm, PaymentExecutionForm
from .models import PlannedPayment, CashTransaction


@transaction.atomic
def save_payment(user, payment):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    if payment.account.owner_id != user.pk:
        raise PermissionDenied
    if payment.pk and not PlannedPayment.objects.filter(pk=payment.pk, account__owner=user, status="planned").exists():
        raise ValidationError("Платёж уже проведён или отменён; изменение плана невозможно.")
    payment.full_clean()
    payment.save()


@transaction.atomic
def execute_payment(user, pk, occurred_on):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    payment = get_object_or_404(PlannedPayment.objects.select_for_update(), pk=pk, account__owner=user)
    if payment.status == "paid":
        return payment.transaction
    if payment.status != "planned":
        raise ValidationError("Отменённый платёж нельзя провести.")
    entry = CashTransaction(account=payment.account, category=payment.category, counterparty=payment.counterparty,
        project=payment.project, amount=payment.amount, description=payment.description, occurred_on=occurred_on)
    entry.full_clean()
    entry.save()
    payment.status, payment.transaction = "paid", entry
    payment.save(update_fields=["status", "transaction"])
    return entry


@login_required
def payment_list(request):
    state = request.GET.get("state", "planned")
    state = state if state in {"planned", "paid", "cancelled", "all"} else "planned"
    entries = PlannedPayment.objects.filter(account__owner=request.user).select_related("account", "category")
    totals = {"income": Decimal("0.00"), "expense": Decimal("0.00")}
    for kind, amount in entries.filter(status="planned").values_list("category__kind", "amount"):
        totals[kind] += amount
    if state != "all":
        entries = entries.filter(status=state)
    return render(request, "cashflow/payments.html", {"page": Paginator(entries, 25).get_page(request.GET.get("page")),
        "state": state, "totals": totals, "today": timezone.localdate()})


@login_required
def payment_edit(request, pk=None):
    instance = get_object_or_404(PlannedPayment, pk=pk, account__owner=request.user, status="planned") if pk else None
    form = PaymentForm(request.POST if request.method == "POST" else None, instance=instance, owner=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            save_payment(request.user, form.save(commit=False))
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("payment_list")
    return render(request, "cashflow/payment_form.html", {"form": form, "heading": "Плановый платёж"})


@login_required
def payment_execute(request, pk):
    payment = get_object_or_404(PlannedPayment, pk=pk, account__owner=request.user)
    form = PaymentExecutionForm(request.POST if request.method == "POST" else None, initial={"occurred_on": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        try:
            execute_payment(request.user, pk, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, "Платёж проведён однократно и включён в фактические операции.")
            return redirect("payment_list")
    return render(request, "cashflow/payment_form.html", {"form": form, "heading": "Провести платёж", "payment": payment})


@login_required
@require_POST
@transaction.atomic
def payment_cancel(request, pk):
    get_user_model().objects.select_for_update().get(pk=request.user.pk)
    payment = get_object_or_404(PlannedPayment, pk=pk, account__owner=request.user)
    if payment.status == "planned":
        payment.status = "cancelled"
        payment.save(update_fields=["status"])
    return redirect("payment_list")
