"""Календарный сценарий ликвидности, без статистического прогноза."""
from datetime import timedelta
from decimal import Decimal
from django import forms
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from .access import get_scope
from .documents import report_scope
from .models import BankAccount
from .reconciliation import balance_at


class ForecastForm(forms.Form):
    account = forms.ModelChoiceField(label="Счёт", queryset=BankAccount.objects.none())
    days = forms.IntegerField(label="Горизонт, дней", min_value=1, max_value=90, initial=30)

    def __init__(self, *args, scope, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = scope.catalog(BankAccount)


def liquidity_scenario(scope, account_id, days, today=None):
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("Горизонт — от 1 до 90 дней.")
    today = today or timezone.localdate()
    account = get_object_or_404(scope.catalog(BankAccount), pk=account_id)
    opening = balance_at(scope, account, today)
    flows = {}
    overdue = 0
    for payment in scope.payments().filter(account=account, status="planned",
            due_on__lte=today+timedelta(days=days)).select_related("category").iterator():
        day = max(today, payment.due_on)
        totals = flows.setdefault(day, {"income": Decimal(0), "expense": Decimal(0)})
        totals[payment.category.kind] += payment.amount
        overdue += payment.due_on < today
    balance = opening
    rows = []
    for offset in range(days+1):
        day = today+timedelta(days=offset)
        totals = flows.get(day, {"income": Decimal(0), "expense": Decimal(0)})
        balance += totals["income"]-totals["expense"]
        rows.append({"date": day, **totals, "balance": balance})
    return {"account": account, "opening": opening, "rows": rows,
        "minimum": min([opening]+[row["balance"] for row in rows]),
        "first_negative": today if opening < 0 else next((row["date"] for row in rows if row["balance"] < 0), None),
        "overdue": overdue}


@login_required
def forecast(request):
    scope = get_scope(request)
    form = ForecastForm(request.GET or None, scope=scope)
    report = None
    if form.is_bound and form.is_valid():
        with report_scope(request.user, scope) as locked_scope:
            report = liquidity_scenario(locked_scope, form.cleaned_data["account"].pk, form.cleaned_data["days"])
    return render(request, "cashflow/forecast.html", {"form": form, "report": report})
