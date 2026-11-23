"""Согласованные остатки всех счетов на выбранную фактическую дату."""
import csv
import io
from decimal import Decimal
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from .access import get_scope
from .documents import report_scope
from .exports import spreadsheet_text
from .models import AccountReconciliation, BankAccount
from .money import CashSum
from .transfers import scoped_transfers

MAX_ACCOUNTS = 2000


class PositionForm(forms.Form):
    day = forms.DateField(label='Остатки на дату', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'}))

    def clean_day(self):
        day = self.cleaned_data['day']
        if day.year < 1900 or day > timezone.localdate():
            raise forms.ValidationError('Выберите фактическую дату с 1900 года до сегодняшнего дня.')
        return day


def account_positions(user, scope, day):
    form = PositionForm({'day':day})
    if not form.is_valid():
        raise ValidationError(form.errors.as_text())
    with report_scope(user, scope) as scope:
        accounts = list(scope.catalog(BankAccount).order_by('name','pk')[:MAX_ACCOUNTS+1])
        if len(accounts)>MAX_ACCOUNTS:
            raise ValidationError('Больше 2000 счетов: полный снимок не сформирован.')
        changes = {row['account_id']:row for row in scope.entries().filter(occurred_on__lte=day).order_by().values('account_id').annotate(
            income=CashSum('amount',filter=Q(category__kind='income')), expense=CashSum('amount',filter=Q(category__kind='expense')))}
        transfers = scoped_transfers(scope).filter(occurred_on__lte=day).order_by()
        incoming = {row['to_account_id']:row['total'] for row in transfers.values('to_account_id').annotate(total=CashSum('amount'))}
        outgoing = {row['from_account_id']:row['total'] for row in transfers.values('from_account_id').annotate(total=CashSum('amount'))}
        snapshots = {}
        for item in AccountReconciliation.objects.filter(account__in=accounts,checked_on=day).order_by('-created_at','-pk'):
            snapshots.setdefault(item.account_id,item)
        rows = []
        for account in accounts:
            changed = changes.get(account.pk,{})
            income, expense = changed.get('income') or Decimal(0), changed.get('expense') or Decimal(0)
            transfer_in, transfer_out = incoming.get(account.pk,Decimal(0)), outgoing.get(account.pk,Decimal(0))
            balance = account.opening_balance+income-expense+transfer_in-transfer_out
            saved = snapshots.get(account.pk)
            rows.append({'account':account,'opening':account.opening_balance,'income':income,'expense':expense,
                'transfer_in':transfer_in,'transfer_out':transfer_out,'balance':balance,'negative':balance<0,
                'snapshot':saved,'changed_after_snapshot':bool(saved and saved.ledger_balance!=balance),
                'difference':saved.statement_balance-balance if saved else None})
        totals = {key:sum((row[key] for row in rows),Decimal(0)) for key in ['opening','income','expense','transfer_in','transfer_out','balance']}
        return {'day':day,'rows':rows,'totals':totals,'scope':scope.name,
                'negative_count':sum(row['negative'] for row in rows),'generated_at':timezone.now()}


def positions_csv(report):
    stream = io.StringIO(newline='');writer=csv.writer(stream,delimiter=';',lineterminator='\n')
    fields = ['opening','income','expense','transfer_in','transfer_out','balance']
    writer.writerow(['type','account_id','account',*fields,'statement_balance','difference','changed_after_snapshot','day','generated_at'])
    metadata = [report['day'].isoformat(),report['generated_at'].isoformat()]
    writer.writerow(['summary','','Все счета',*[f"{report['totals'][key]:.2f}" for key in fields],'','','',*metadata])
    for row in report['rows']:
        writer.writerow(['account',row['account'].pk,spreadsheet_text(row['account'].name),*[f'{row[key]:.2f}' for key in fields],
            f"{row['snapshot'].statement_balance:.2f}" if row['snapshot'] else '',
            f"{row['difference']:.2f}" if row['difference'] is not None else '',str(row['changed_after_snapshot']),*metadata])
    response=HttpResponse('\ufeff'+stream.getvalue(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="account-positions.csv"';response['Cache-Control']='private, no-store'
    return response


@login_required
def positions(request):
    form=PositionForm(request.GET if request.GET else {'day':timezone.localdate()})
    report=page=None
    if form.is_valid():
        try:report=account_positions(request.user,get_scope(request),**form.cleaned_data)
        except ValidationError as exc:form.add_error(None,exc)
        else:page=Paginator(report['rows'],50).get_page(request.GET.get('page'))
    if request.GET.get('format')=='csv':
        return positions_csv(report) if report else HttpResponseBadRequest('Проверьте дату и ограничения снимка.')
    return render(request,'cashflow/positions.html',{'form':form,'report':report,'page':page})
