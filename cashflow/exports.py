"""Выгрузка журнала с теми же границами доступа и фильтрами, что на экране."""
import csv
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, StreamingHttpResponse
from .forms import TransactionFilterForm
from .services import filtered_transactions
from .access import get_scope


def spreadsheet_text(value):
    text = str(value or "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


class CsvBuffer:
    def write(self, value):
        return value


@login_required
def transaction_export(request):
    form = TransactionFilterForm(request.GET, owner=request.user, scope=get_scope(request))
    if not form.is_valid():
        return HttpResponseBadRequest(form.errors.as_text(), content_type="text/plain; charset=utf-8")
    entries = filtered_transactions(request.user, form.cleaned_data, scope=get_scope(request))
    def rows():
        writer = csv.writer(CsvBuffer(), delimiter=";", lineterminator="\n")
        yield "\ufeff"
        yield writer.writerow(["date", "account", "category", "kind", "amount", "description", "counterparty", "project"])
        for entry in entries.iterator(chunk_size=1000):
            yield writer.writerow([entry.occurred_on.isoformat(), spreadsheet_text(entry.account.name),
                spreadsheet_text(entry.category.name), entry.category.kind, f"{entry.amount:.2f}",
                spreadsheet_text(entry.description), spreadsheet_text(entry.counterparty), spreadsheet_text(entry.project)])
    response = StreamingHttpResponse(rows(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="transactions.csv"'
    return response
