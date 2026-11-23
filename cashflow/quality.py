"""Поиск одинаковых фактических записей без изменения учёта."""
import csv
import io
from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlencode
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from .access import get_scope
from .documents import report_scope
from .exports import spreadsheet_text
from .forms import AnalysisPeriodForm
from .models import BankAccount

MAX_OPERATIONS = 10000


class QualityForm(AnalysisPeriodForm):
    account = forms.ModelChoiceField(label='Счёт', queryset=BankAccount.objects.none(), required=False, empty_label='Все счета')

    def __init__(self, *args, scope, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['account'].queryset = scope.catalog(BankAccount)


def duplicate_report(user, scope, start, end, account=None):
    with report_scope(user, scope) as scope:
        form = QualityForm({'start': start, 'end': end, 'account': account.pk if account else ''}, scope=scope)
        if not form.is_valid():
            raise ValidationError(form.errors.as_text())
        entries = scope.entries().filter(occurred_on__range=(start, end))
        if account:
            entries = entries.filter(account=account)
        if entries.count() > MAX_OPERATIONS:
            raise ValidationError('Больше 10 000 операций: сократите период или выберите счёт. Частичный поиск не выполняется.')
        buckets = defaultdict(list)
        fields = ['occurred_on', 'account_id', 'category_id', 'counterparty_id', 'project_id', 'amount', 'description']
        for row in entries.order_by('pk').values('pk', 'account__name', 'category__name', 'category__kind', *fields):
            buckets[tuple(row[field] for field in fields)].append(row)
        groups = []
        for rows in buckets.values():
            if len(rows) < 2:
                continue
            first = rows[0]
            groups.append({'date': first['occurred_on'], 'account': first['account__name'],
                'category': first['category__name'], 'kind': first['category__kind'],
                'amount': first['amount'], 'description': first['description'], 'count': len(rows),
                'ids': [row['pk'] for row in rows], 'preview_ids': [row['pk'] for row in rows[:20]],
                'possible_extra': first['amount']*(len(rows)-1)})
        groups.sort(key=lambda group: (-group['count'], group['date'], group['ids'][0]))
        extra = {kind: sum((group['possible_extra'] for group in groups if group['kind']==kind), Decimal(0))
                 for kind in ['income', 'expense']}
        return {'start': start, 'end': end, 'account': account, 'scope': scope.name, 'groups': groups,
                'checked': entries.count(), 'matched': sum(group['count'] for group in groups),
                'possible_extra': extra, 'generated_at': timezone.now()}


def quality_csv(report):
    stream = io.StringIO(newline=''); writer = csv.writer(stream, delimiter=';', lineterminator='\n')
    writer.writerow(['date', 'account', 'category', 'kind', 'amount', 'description', 'matching_records', 'operation_ids', 'possible_extra', 'snapshot'])
    for group in report['groups']:
        writer.writerow([group['date'].isoformat(), spreadsheet_text(group['account']), spreadsheet_text(group['category']),
            group['kind'], f"{group['amount']:.2f}", spreadsheet_text(group['description']), group['count'],
            ','.join(str(value) for value in group['ids']), f"{group['possible_extra']:.2f}", report['generated_at'].isoformat()])
    response = HttpResponse('\ufeff'+stream.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="possible-duplicates.csv"'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
def quality(request):
    scope = get_scope(request); today = timezone.localdate()
    form = QualityForm(request.GET if request.GET else {'start': today.replace(day=1), 'end': today}, scope=scope)
    report = page = None; query = ''
    if form.is_valid():
        try:
            report = duplicate_report(request.user, scope, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            page = Paginator(report['groups'], 50).get_page(request.GET.get('page'))
            query = urlencode({'start': report['start'].isoformat(), 'end': report['end'].isoformat(),
                               'account': report['account'].pk if report['account'] else ''})
    if request.GET.get('format')=='csv':
        return quality_csv(report) if report else HttpResponseBadRequest('Проверьте период, счёт и ограничения поиска.')
    return render(request, 'cashflow/quality.html', {'form': form, 'report': report, 'page': page, 'query': query})
