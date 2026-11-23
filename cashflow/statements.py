"""Согласованная выписка счёта с внешними операциями и своими переводами."""
from datetime import timedelta
from decimal import Decimal
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from .access import get_scope
from .documents import report_scope
from .models import BankAccount
from .reconciliation import balance_at
from .transfers import scoped_transfers
from django.db.models import Q

MAX_ROWS = 5000


class StatementForm(forms.Form):
    account=forms.ModelChoiceField(label='Счёт',queryset=BankAccount.objects.none())
    start=forms.DateField(label='С даты',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    end=forms.DateField(label='По дату',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))

    def __init__(self,*args,scope,**kwargs):
        super().__init__(*args,**kwargs);self.fields['account'].queryset=scope.catalog(BankAccount)

    def clean(self):
        values=super().clean();start,end=values.get('start'),values.get('end')
        if start and end and (not 0<=(end-start).days<366 or start.year<1900 or end>timezone.localdate()):
            raise forms.ValidationError('Выберите фактический период от1 до366 дней начиная с1900года.')
        return values


def account_statement(user,scope,account,start,end):
    with report_scope(user,scope) as scope:
        account=get_object_or_404(scope.catalog(BankAccount),pk=account.pk)
        form=StatementForm({'account':account.pk,'start':start,'end':end},scope=scope)
        if not form.is_valid():raise ValidationError(form.errors.as_text())
        operations=scope.entries().filter(account=account,occurred_on__range=(start,end)).select_related('category','counterparty')
        transfers=scoped_transfers(scope).filter(Q(from_account=account)|Q(to_account=account),occurred_on__range=(start,end)).select_related('from_account','to_account')
        if operations.count()+transfers.count()>MAX_ROWS:raise ValidationError('Больше5000 строк: сократите период. Неполная выписка не формируется.')
        rows=[]
        for item in operations:
            incoming=item.category.kind=='income'
            rows.append({'date':item.occurred_on,'created_at':item.created_at,'source':'operation','id':item.pk,
                'label':item.category.name,'description':item.description,'counterparty':str(item.counterparty or ''),
                'credit':item.amount if incoming else Decimal(0),'debit':Decimal(0) if incoming else item.amount})
        for item in transfers:
            incoming=item.to_account_id==account.pk
            peer=item.from_account if incoming else item.to_account
            rows.append({'date':item.occurred_on,'created_at':item.created_at,'source':'transfer','id':item.pk,
                'label':'Перевод между своими счетами','description':item.description,'counterparty':peer.name,
                'credit':item.amount if incoming else Decimal(0),'debit':Decimal(0) if incoming else item.amount})
        rows.sort(key=lambda row:(row['date'],row['created_at'],row['source'],row['id']))
        opening=balance_at(scope,account,start-timedelta(days=1));balance=opening
        credits=debits=Decimal(0)
        for row in rows:
            credits+=row['credit'];debits+=row['debit'];balance+=row['credit']-row['debit'];row['balance']=balance
        return {'account':account,'start':start,'end':end,'opening':opening,'closing':balance,'credits':credits,
            'debits':debits,'rows':rows,'generated_at':timezone.now(),'scope_name':scope.name}


@login_required
def statement_view(request):
    scope=get_scope(request);today=timezone.localdate()
    form=StatementForm(request.GET if request.GET else None,scope=scope,initial={'start':today.replace(day=1),'end':today})
    report=None;page=None
    if form.is_bound and form.is_valid():
        try:report=account_statement(request.user,scope,**form.cleaned_data)
        except ValidationError as exc:form.add_error(None,exc)
        else:page=Paginator(report['rows'],100).get_page(request.GET.get('page'))
    query=request.GET.copy();query.pop('page',None)
    return render(request,'cashflow/statement.html',{'form':form,'report':report,'page':page,'query':query.urlencode()})
