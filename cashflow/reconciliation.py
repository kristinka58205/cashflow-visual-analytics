import csv
import io
from decimal import Decimal
from django import forms
from django.db.models import Q
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.http import HttpResponse
from .exports import spreadsheet_text
from django.utils import timezone
from .access import get_scope, write_scope
from .audit import record_event
from .documents import report_scope
from .models import AccountReconciliation, BankAccount, CashTransfer


def balance_at(scope, account, checked_on):
    account = get_object_or_404(scope.catalog(BankAccount), pk=account.pk)
    balance = account.opening_balance
    for kind, amount in scope.entries().filter(account=account, occurred_on__lte=checked_on).values_list("category__kind", "amount").iterator():
        balance += amount if kind == "income" else -amount
    for source, target, amount in CashTransfer.objects.filter(occurred_on__lte=checked_on).filter(
            Q(from_account=account) | Q(to_account=account)).values_list("from_account_id", "to_account_id", "amount").iterator():
        balance += amount if target == account.pk else -amount
    return balance


def reconcile(user, scope, account_id, checked_on, statement_balance):
    with write_scope(user, scope) as locked_scope:
        account = get_object_or_404(locked_scope.catalog(BankAccount), pk=account_id)
        form = ReconciliationForm({"account": account.pk, "checked_on": checked_on,
            "statement_balance": statement_balance}, scope=locked_scope)
        if not form.is_valid():
            raise ValidationError(form.errors.as_text())
        actual = balance_at(locked_scope, account, checked_on)
        snapshot, created = AccountReconciliation.objects.get_or_create(account=account,
            checked_on=checked_on, statement_balance=form.cleaned_data["statement_balance"],
            ledger_balance=actual, defaults={"actor": user})
        if created:
            record_event(locked_scope, "created", "reconciliation", snapshot.pk,
                f"Сверка {account.name} за {checked_on}: учёт {actual:.2f}; выписка {snapshot.statement_balance:.2f}")
        return snapshot, created


class ReconciliationForm(forms.Form):
    account = forms.ModelChoiceField(label="Счёт", queryset=BankAccount.objects.none())
    checked_on = forms.DateField(label="Дата остатка", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    statement_balance = forms.DecimalField(label="Остаток по выписке, ₽", max_digits=18, decimal_places=2)

    def __init__(self, *args, scope, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = scope.catalog(BankAccount)

    def clean_checked_on(self):
        value = self.cleaned_data["checked_on"]
        if value > timezone.localdate():
            raise forms.ValidationError("Сверяйте фактический остаток не позже сегодняшнего дня.")
        return value


MAX_EXPORT=2000


class ReconciliationFilterForm(forms.Form):
    account=forms.ModelChoiceField(label='Счёт сверки',queryset=BankAccount.objects.none(),required=False)
    start=forms.DateField(label='Дата остатка с',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='Дата остатка по',required=False,widget=forms.DateInput(attrs={'type':'date'}))

    def __init__(self,*args,scope,**kwargs):
        super().__init__(*args,**kwargs);self.fields['account'].queryset=scope.catalog(BankAccount)

    def clean(self):
        values=super().clean()
        if values.get('start') and values.get('end') and values['start']>values['end']:
            raise forms.ValidationError('Начало периода должно быть не позже конца.')
        return values


def reconciliation_csv(query):
    if query.count()>MAX_EXPORT:raise ValidationError('Больше2000 сверок. Уточните счёт или период; частичная выгрузка не формируется.')
    stream=io.StringIO();writer=csv.writer(stream,delimiter=';',lineterminator='\n')
    writer.writerow(['checked_on','account','statement_balance','ledger_balance_at_check','difference_at_check','created_at','actor'])
    for row in query:
        writer.writerow([row.checked_on.isoformat(),spreadsheet_text(row.account.name),
            f'{row.statement_balance:.2f}',f'{row.ledger_balance:.2f}',f'{row.statement_balance-row.ledger_balance:.2f}',
            row.created_at.isoformat(),spreadsheet_text(row.actor.get_username())])
    response=HttpResponse('\ufeff'+stream.getvalue(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="reconciliation-snapshots.csv"'
    response['Cache-Control']='private, no-store'
    return response


@login_required
def reconciliation_list(request):
    scope = get_scope(request)
    form = ReconciliationForm(request.POST if request.method == "POST" else None, scope=scope,
        initial={"checked_on": timezone.localdate()})
    if request.method == "POST":
        if not scope.can_write:
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if form.is_valid():
            snapshot, created = reconcile(request.user, scope, form.cleaned_data["account"].pk,
                form.cleaned_data["checked_on"], form.cleaned_data["statement_balance"])
            messages.success(request, "Снимок сверки сохранён." if created else "Такая сверка уже сохранена.")
            return redirect("reconciliation_list")
    filters=ReconciliationFilterForm(request.GET,scope=scope)
    status=200
    with report_scope(request.user, scope) as locked_scope:
        query = AccountReconciliation.objects.filter(account__in=locked_scope.catalog(BankAccount)).select_related("account", "actor")
        if filters.is_valid():
            values=filters.cleaned_data
            if values.get('account'):query=query.filter(account=values['account'])
            if values.get('start'):query=query.filter(checked_on__gte=values['start'])
            if values.get('end'):query=query.filter(checked_on__lte=values['end'])
            if request.method=='GET' and request.GET.get('format')=='csv':
                try:return reconciliation_csv(query)
                except ValidationError as exc:filters.add_error(None,exc);status=400
        else:
            query=query.none();status=400
        page = Paginator(query, 25).get_page(request.GET.get("page"))
        for snapshot in page:
            snapshot.difference = snapshot.statement_balance-snapshot.ledger_balance
            snapshot.current_balance = balance_at(locked_scope, snapshot.account, snapshot.checked_on)
            snapshot.stale = snapshot.current_balance != snapshot.ledger_balance
    params=request.GET.copy();params.pop('page',None);params.pop('format',None)
    page_query=params.urlencode();params['format']='csv'
    return render(request, "cashflow/reconciliation.html", {"form": form, "page": page,"filters":filters,
        "page_query":page_query,"export_query":params.urlencode()},status=status)
