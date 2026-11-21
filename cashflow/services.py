"""Денежная арифметика выполняется Decimal, без округлений float."""
from decimal import Decimal
from django.db.models import Sum, Q
from .models import BankAccount, CashCategory, CashTransaction
from datetime import date


def cashflow_analysis(user, start, end):
    summary = financial_summary(user, start, end)
    monthly = {}
    cursor = start.replace(day=1)
    while cursor <= end:
        monthly[cursor] = {"month": cursor, "income": Decimal("0.00"), "expense": Decimal("0.00")}
        if (cursor.year, cursor.month) == (end.year, end.month):
            break
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    categories = {}
    entries = CashTransaction.objects.filter(account__owner=user, occurred_on__range=(start, end))
    for entry in entries.values("occurred_on", "category_id", "category__name", "category__kind", "amount"):
        kind = entry["category__kind"]
        monthly[entry["occurred_on"].replace(day=1)][kind] += entry["amount"]
        item = categories.setdefault(entry["category_id"], {"name": entry["category__name"], "kind": kind, "amount": Decimal("0.00"), "count": 0})
        item["amount"] += entry["amount"]
        item["count"] += 1
    peak = max((max(item["income"], item["expense"]) for item in monthly.values()), default=Decimal("0"))
    balance = summary["opening"]
    for item in monthly.values():
        item["net"] = item["income"] - item["expense"]
        balance += item["net"]
        item["closing"] = balance
        for kind in ["income", "expense"]:
            item[kind + "_width"] = f"{item[kind] * 100 / peak:.2f}" if peak else "0"
    for item in categories.values():
        total = summary[item["kind"]]
        item["share"] = item["amount"] * 100 / total if total else Decimal("0")
    return {"summary": summary, "months": list(monthly.values()),
        "categories": sorted(categories.values(), key=lambda item: (-item["amount"], item["name"]))}


def filtered_transactions(user, values):
    entries = CashTransaction.objects.filter(account__owner=user).select_related("account", "category", "counterparty", "project")
    for field in ["account", "category", "counterparty", "project"]:
        if values.get(field):
            entries = entries.filter(**{field: values[field]})
    for field, lookup in [("start", "occurred_on__gte"), ("end", "occurred_on__lte"),
                          ("minimum", "amount__gte"), ("maximum", "amount__lte"), ("kind", "category__kind")]:
        if values.get(field) is not None and values.get(field) != "":
            entries = entries.filter(**{lookup: values[field]})
    if values.get("search"):
        entries = entries.filter(Q(description__icontains=values["search"]) | Q(counterparty__name__icontains=values["search"]))
    ordering = {"newest": "-occurred_on", "oldest": "occurred_on", "largest": "-amount", "smallest": "amount"}
    return entries.order_by(ordering.get(values.get("order"), "-occurred_on"), "-pk")


def transaction_totals(entries):
    result = entries.aggregate(income=Sum("amount", filter=Q(category__kind="income")),
                               expense=Sum("amount", filter=Q(category__kind="expense")))
    result = {key: value or Decimal("0.00") for key, value in result.items()}
    return {**result, "net": result["income"] - result["expense"]}


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
