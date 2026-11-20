"""Денежная арифметика выполняется Decimal, без округлений float."""
from decimal import Decimal
from django.db.models import Sum
from .models import BankAccount, CashCategory, CashTransaction


def financial_summary(user, start=None, end=None):
    entries = CashTransaction.objects.filter(account__owner=user)
    if start:
        entries = entries.filter(occurred_on__gte=start)
    if end:
        entries = entries.filter(occurred_on__lte=end)
    income = entries.filter(category__kind=CashCategory.Kind.INCOME).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    expense = entries.filter(category__kind=CashCategory.Kind.EXPENSE).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    opening = BankAccount.objects.filter(owner=user).aggregate(total=Sum("opening_balance"))["total"] or Decimal("0.00")
    if start:
        before = CashTransaction.objects.filter(account__owner=user, occurred_on__lt=start)
        opening += before.filter(category__kind="income").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        opening -= before.filter(category__kind="expense").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return {"income": income, "expense": expense, "net": income-expense, "opening": opening, "closing": opening+income-expense}
