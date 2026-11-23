"""Внешние денежные потоки в разрезе проектов и контрагентов."""
import csv
import io
from decimal import Decimal
from urllib.parse import urlencode
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from .access import get_scope
from .documents import report_scope
from .exports import spreadsheet_text
from .forms import AnalysisPeriodForm
from .money import CashSum

MAX_GROUPS = 2000


class DimensionForm(AnalysisPeriodForm):
    dimension = forms.ChoiceField(label='Группировка', choices=[('project', 'Проекты'), ('counterparty', 'Контрагенты')])
    order = forms.ChoiceField(label='Порядок', choices=[('net', 'По чистому потоку'), ('income', 'По поступлениям'), ('expense', 'По расходам'), ('name', 'По названию')])


def dimension_report(user, scope, **values):
    form = DimensionForm(values)
    if not form.is_valid():
        raise ValidationError(form.errors.as_text())
    values = form.cleaned_data
    dimension = values['dimension']
    with report_scope(user, scope) as scope:
        entries = scope.entries().filter(occurred_on__range=(values['start'], values['end']))
        query = entries.order_by().values(dimension+'_id', dimension+'__name').annotate(
            count=Count('pk'), income=CashSum('amount', filter=Q(category__kind='income')),
            expense=CashSum('amount', filter=Q(category__kind='expense')))
        groups = list(query[:MAX_GROUPS+1])
        if len(groups) > MAX_GROUPS:
            raise ValidationError('Больше 2000 групп: сократите период. Частичный отчёт не формируется.')
        rows = []
        for group in groups:
            identifier = group[dimension+'_id']
            income, expense = group['income'] or Decimal(0), group['expense'] or Decimal(0)
            filters = {'start': values['start'].isoformat(), 'end': values['end'].isoformat()}
            filters[dimension if identifier is not None else 'unassigned'] = identifier if identifier is not None else dimension
            rows.append({'id': identifier, 'name': group[dimension+'__name'] or ('Без проекта' if dimension=='project' else 'Без контрагента'),
                         'income': income, 'expense': expense, 'net': income-expense,
                         'count': group['count'], 'operations_query': urlencode(filters)})
        totals = {key: sum((row[key] for row in rows), Decimal(0)) for key in ['income', 'expense', 'net']}
        for row in rows:
            for key in ['income', 'expense']:
                row[key+'_width'] = f'{row[key]*100/totals[key]:.2f}' if totals[key] else '0'
        rows.sort(key=lambda row: ((row['name'].casefold(), row['id'] or 0) if values['order']=='name'
                                  else (-row[values['order']], row['name'].casefold(), row['id'] or 0)))
        return {**values, 'scope': scope.name, 'rows': rows, 'totals': totals,
                'count': sum(row['count'] for row in rows), 'generated_at': timezone.now()}


def dimension_csv(report):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter=';', lineterminator='\n')
    writer.writerow(['row_type', 'dimension', 'id', 'name', 'operations', 'income', 'expense', 'net', 'start', 'end', 'generated_at'])
    metadata = [report['start'].isoformat(), report['end'].isoformat(), report['generated_at'].isoformat()]
    writer.writerow(['summary', report['dimension'], '', 'Итого', report['count'],
                     *[f"{report['totals'][key]:.2f}" for key in ['income', 'expense', 'net']], *metadata])
    for row in report['rows']:
        writer.writerow(['group', report['dimension'], row['id'] if row['id'] is not None else '',
                         spreadsheet_text(row['name']), row['count'],
                         *[f'{row[key]:.2f}' for key in ['income', 'expense', 'net']], *metadata])
    response = HttpResponse('\ufeff'+stream.getvalue(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="cashflow-dimensions.csv"'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
def dimensions(request):
    today = timezone.localdate()
    form = DimensionForm(request.GET if request.GET else {'start': today.replace(day=1), 'end': today, 'dimension': 'project', 'order': 'net'})
    report = page = None
    query = ''
    if form.is_valid():
        try:
            report = dimension_report(request.user, get_scope(request), **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            page = Paginator(report['rows'], 50).get_page(request.GET.get('page'))
            query = urlencode({key: value.isoformat() if hasattr(value, 'isoformat') else value for key, value in form.cleaned_data.items()})
    if request.GET.get('format')=='csv':
        return dimension_csv(report) if report else HttpResponseBadRequest('Проверьте период и параметры группировки.')
    return render(request, 'cashflow/dimensions.html', {'form': form, 'report': report, 'page': page, 'query': query})
