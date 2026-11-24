import calendar
from collections import defaultdict
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .forms import BudgetForm, BudgetMonthForm, RevisionForm
from .revisions import require_revision
from django.core.exceptions import ValidationError
from .models import ExpenseBudget, CashTransaction
from .access import LedgerScope, get_scope, financial_write
from .audit import record_event


def budget_overview(user, month, scope=None):
    scope = scope or LedgerScope(user)
    end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    spending = defaultdict(lambda: Decimal("0.00"))
    for category_id, amount in scope.entries().filter(category__kind="expense",
            occurred_on__range=(month, end)).values_list("category_id", "amount"):
        spending[category_id] += amount
    rows = []
    for budget in scope.budgets().filter(month_start=month).select_related("category").order_by("category__name"):
        actual = spending[budget.category_id]
        rows.append({"budget": budget, "actual": actual, "remaining": budget.limit-actual, "exceeded": actual > budget.limit})
    return rows


@login_required
def budget_list(request):
    form = BudgetMonthForm(request.GET or {"month": timezone.localdate().strftime("%Y-%m")})
    rows = budget_overview(request.user, form.cleaned_data["month"], scope=get_scope(request)) if form.is_valid() else []
    return render(request, "cashflow/budgets.html", {"month_form": form, "rows": rows})


@login_required
@financial_write
def budget_edit(request, pk=None):
    instance = get_object_or_404(get_scope(request).budgets(), pk=pk) if pk else None
    form = BudgetForm(request.POST if request.method == "POST" else None, owner=request.user, scope=get_scope(request), instance=instance)
    if request.method == "POST" and form.is_valid():
        try:
            if pk:require_revision(instance,form.cleaned_data['expected_revision'])
        except ValidationError as exc:
            form.add_error(None,exc)
        else:
            if pk:instance.revision+=1
            saved=form.save()
            record_event(get_scope(request), "updated" if pk else "created", "budget", saved.pk, f"{saved.category}: {saved.limit:.2f} ₽, {saved.month_start:%m.%Y}")
            messages.success(request, "Месячный лимит сохранён.")
            return redirect("budget_list")
    return render(request, "cashflow/budget_form.html", {"form": form})


@login_required
@financial_write
def budget_delete(request, pk):
    budget = get_object_or_404(get_scope(request).budgets(), pk=pk)
    form=RevisionForm(request.POST if request.method=='POST' else None,initial={'expected_revision':budget.revision})
    if request.method=='POST' and form.is_valid():
        try:require_revision(budget,form.cleaned_data['expected_revision'])
        except ValidationError as exc:form.add_error(None,exc)
        else:
            object_id, summary = budget.pk, f"{budget.category}: {budget.limit:.2f} ₽"
            budget.delete()
            record_event(get_scope(request), "deleted", "budget", object_id, summary)
            return redirect("budget_list")
    return render(request, "cashflow/budget_form.html", {"budget": budget,"form":form})
