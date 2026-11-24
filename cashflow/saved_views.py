"""Сохранённые фильтры: всегда свой пользователь и текущая рабочая область."""
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, TooManyFieldsSent
from django.http import QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from .access import get_scope
from .documents import report_scope
from .forms import TransactionFilterForm
from .models import SavedTransactionView

LIMIT = 50


class SaveViewForm(forms.Form):
    name = forms.CharField(label="Название подборки", max_length=80)
    query = forms.CharField(required=False, max_length=4096, widget=forms.HiddenInput)


def own_views(user, scope):
    return SavedTransactionView.objects.filter(owner=user, organization_id=scope.organization_id)


def normalized_filters(user, scope, values):
    if not isinstance(values, dict) or set(values) - set(TransactionFilterForm.base_fields):
        raise ValidationError("Подборка содержит неизвестные фильтры. Создайте её заново.")
    if any(not isinstance(value, str) for value in values.values()):
        raise ValidationError("Повреждены значения фильтров. Создайте подборку заново.")
    form = TransactionFilterForm(values, owner=user, scope=scope)
    if not form.is_valid():
        raise ValidationError("Фильтры недействительны или справочник больше недоступен. Данные не показаны; создайте подборку заново.")
    result = {}
    for key, value in form.cleaned_data.items():
        if value is None or value == "":
            continue
        if hasattr(value, "pk"):
            value = str(value.pk)
        elif isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = format(value, ".2f")
        result[key] = str(value)
    return result


@login_required
def saved_view_list(request):
    with report_scope(request.user, get_scope(request)) as scope:
        rows = list(own_views(request.user, scope))
    return render(request, "cashflow/saved_views.html", {"saved_views": rows})


@login_required
@require_POST
def saved_view_save(request):
    form = SaveViewForm(request.POST)
    with report_scope(request.user, get_scope(request)) as scope:
        rows = own_views(request.user, scope)
        if form.is_valid():
            try:
                query = QueryDict(form.cleaned_data["query"], mutable=True)
                query.pop("page", None)
                if any(len(items) != 1 for _, items in query.lists()):
                    raise ValidationError("Каждый фильтр должен быть указан один раз.")
                filters = normalized_filters(request.user, scope, query.dict())
                if rows.count() >= LIMIT:
                    raise ValidationError("Достигнут предел 50 подборок в этой рабочей области. Удалите ненужные.")
                if rows.filter(name=form.cleaned_data["name"]).exists():
                    raise ValidationError("Такое название уже используется в этой рабочей области.")
                SavedTransactionView.objects.create(owner=request.user, organization=scope.organization,
                    name=form.cleaned_data["name"], filters=filters)
                return redirect("saved_view_list")
            except (ValidationError, TooManyFieldsSent) as exc:
                form.add_error(None, exc if isinstance(exc, ValidationError) else "Слишком много параметров фильтра.")
        rows = list(rows)
    return render(request, "cashflow/saved_views.html", {"saved_views": rows, "save_form": form}, status=400)


@login_required
def saved_view_open(request, pk):
    with report_scope(request.user, get_scope(request)) as scope:
        item = get_object_or_404(own_views(request.user, scope), pk=pk)
        try:
            filters = normalized_filters(request.user, scope, item.filters)
        except ValidationError as exc:
            return render(request, "cashflow/saved_views.html", {"saved_views": list(own_views(request.user, scope)), "view_errors": exc.messages}, status=409)
    return redirect(reverse("transaction_list") + "?" + urlencode(filters))


@login_required
@require_POST
def saved_view_delete(request, pk):
    with report_scope(request.user, get_scope(request)) as scope:
        get_object_or_404(own_views(request.user, scope), pk=pk).delete()
    return redirect("saved_view_list")
