from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
import csv
from .access import get_scope
from .models import LedgerEvent


def record_event(scope, action, entity, object_id, summary):
    if not scope.can_write:
        raise PermissionDenied
    return LedgerEvent.objects.create(organization=scope.organization, actor=scope.user,
        actor_name=scope.user.get_username(), action=action, entity=entity, object_id=object_id, summary=str(summary)[:300])


class AuditFilterForm(forms.Form):
    action = forms.ChoiceField(label="Действие", required=False, choices=[("", "Все действия"), *LedgerEvent.Action.choices])
    entity = forms.ChoiceField(label="Объект", required=False, choices=[("", "Все объекты"), *LedgerEvent._meta.get_field("entity").choices])
    start = forms.DateField(label="С даты", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="По дату", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            self.add_error("end", "Конец периода должен быть не раньше начала.")
        return data


def filtered_events(scope, values):
    events = scope.events()
    for key in ["action", "entity"]:
        if values.get(key):
            events = events.filter(**{key: values[key]})
    if values.get("start"):
        events = events.filter(created_at__date__gte=values["start"])
    if values.get("end"):
        events = events.filter(created_at__date__lte=values["end"])
    return events


@login_required
def audit_list(request):
    form = AuditFilterForm(request.GET)
    events = filtered_events(get_scope(request), form.cleaned_data) if form.is_valid() else LedgerEvent.objects.none()
    query = request.GET.copy();query.pop("page", None)
    return render(request, "cashflow/audit.html", {"filters": form,
        "page": Paginator(events, 25).get_page(request.GET.get("page")), "page_query": query.urlencode()})


@login_required
def audit_export(request):
    form = AuditFilterForm(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest(form.errors.as_text(), content_type="text/plain; charset=utf-8")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="ledger-events.csv"'
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";", lineterminator="\n")
    writer.writerow(["date", "actor", "action", "entity", "object_id", "summary"])
    def safe(value):
        value = str(value)
        return "'"+value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    for event in filtered_events(get_scope(request), form.cleaned_data).iterator():
        writer.writerow([event.created_at.isoformat(), safe(event.actor_name), event.action, event.entity, event.object_id, safe(event.summary)])
    return response
