import uuid
from django.core.exceptions import ValidationError
from .operations import create_operation
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from .forms import AccountForm, CategoryForm, TransactionForm, OperationCreateForm, OperationEditForm, CounterpartyForm, ProjectForm, TransactionFilterForm, AnalysisPeriodForm
from .models import BankAccount, CashCategory, CashTransaction, Counterparty, CashProject, PlannedPayment
from .services import financial_summary, filtered_transactions, transaction_totals, cashflow_analysis
from django.core.paginator import Paginator
from django.utils import timezone
from .access import get_scope, financial_write
from .audit import record_event


def home(request):
    if request.user.is_authenticated:
        today = timezone.localdate()
        start = today.replace(day=1)
        return render(request, "cashflow/dashboard.html", {"summary": financial_summary(request.user, start, today, scope=get_scope(request)), "start": start, "end": today})
    return render(request, "cashflow/home.html")


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = UserCreationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save())
        return redirect("home")
    return render(request, "registration/register.html", {"form": form})


@login_required
def catalog(request):
    return render(request, "cashflow/catalog.html", {
        "accounts": get_scope(request).catalog(BankAccount),
        "categories": get_scope(request).catalog(CashCategory),
        "business_catalogs": [
            {"kind": "counterparties", "title": "Контрагенты", "items": get_scope(request).catalog(Counterparty)},
            {"kind": "projects", "title": "Проекты", "items": get_scope(request).catalog(CashProject)},
        ],
    })


def catalog_type(kind):
    if kind == "accounts":
        return BankAccount, AccountForm, "Счёт"
    if kind == "categories":
        return CashCategory, CategoryForm, "Статья"
    if kind == "counterparties":
        return Counterparty, CounterpartyForm, "Контрагент"
    if kind == "projects":
        return CashProject, ProjectForm, "Проект"
    raise Http404


@login_required
@financial_write
def catalog_edit(request, kind, pk=None):
    model, form_class, title = catalog_type(kind)
    instance = get_object_or_404(get_scope(request).catalog(model), pk=pk) if pk else model(owner=request.user, organization=get_scope(request).organization)
    form = form_class(request.POST if request.method == "POST" else None, owner=request.user, scope=get_scope(request), instance=instance)
    if request.method == "POST" and form.is_valid():
        # Проверка уникальности с owner нужна до записи; owner не является полем формы.
        duplicates = get_scope(request).catalog(model).filter(name=form.cleaned_data["name"])
        if model is CashCategory:
            duplicates = duplicates.filter(kind=form.cleaned_data["kind"])
        if duplicates.exclude(pk=instance.pk).exists():
            form.add_error("name", "Такое название уже используется.")
        else:
            saved = form.save()
            record_event(get_scope(request), "updated" if pk else "created", "catalog", saved.pk, f"{title}: {saved}")
            messages.success(request, "Изменения сохранены.")
            return redirect("catalog")
    return render(request, "cashflow/catalog_form.html", {"form": form, "heading": title})


@login_required
@financial_write
def catalog_delete(request, kind, pk):
    model, _, _ = catalog_type(kind)
    instance = get_object_or_404(get_scope(request).catalog(model), pk=pk)
    if request.method == "POST":
        object_id, summary = instance.pk, str(instance)
        try:
            instance.delete()
        except ProtectedError:
            messages.error(request, "Справочник используется в операциях и не может быть удалён.")
        else:
            record_event(get_scope(request), "deleted", "catalog", object_id, summary)
            messages.success(request, "Запись удалена.")
        return redirect("catalog")
    return render(request, "cashflow/confirm_delete.html", {"instance": instance})


@login_required
def transaction_list(request):
    form = TransactionFilterForm(request.GET, owner=request.user, scope=get_scope(request))
    entries = filtered_transactions(request.user, form.cleaned_data, scope=get_scope(request)) if form.is_valid() else CashTransaction.objects.none()
    page = Paginator(entries, 25).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "cashflow/transactions.html", {"page": page, "filters": form,
        "totals": transaction_totals(entries), "page_query": params.urlencode()})


@login_required
@financial_write
def transaction_edit(request, pk=None):
    entry = get_object_or_404(get_scope(request).entries(), pk=pk) if pk else None
    if entry and PlannedPayment.objects.filter(transaction=entry).exists():
        messages.error(request, "Операция проведённого планового платежа защищена от прямого изменения.")
        return redirect("transaction_list")
    form_class = OperationEditForm if pk else OperationCreateForm
    form = form_class(request.POST if request.method == "POST" else None, owner=request.user,
                      scope=get_scope(request), instance=entry, initial={"command_key": uuid.uuid4(), "expected_revision": entry.revision if entry else None})
    if request.method == "POST" and form.is_valid():
        try:
            if pk:
                if form.cleaned_data["expected_revision"] != entry.revision:
                    raise ValidationError("Операция уже изменена в другой вкладке. Откройте актуальную запись заново.")
                form.instance.revision = entry.revision + 1
                saved = form.save()
                record_event(get_scope(request), "updated", "operation", saved.pk, f"{saved.description}: {saved.amount:.2f} ₽")
                created = True
            else:
                saved, created = create_operation(request.user, get_scope(request), form.cleaned_data["command_key"], form.save(commit=False))
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Операция сохранена." if created else "Эта операция уже сохранена.")
            return redirect("transaction_list")
    return render(request, "cashflow/transaction_form.html", {"form": form})


@login_required
@financial_write
def transaction_delete(request, pk):
    entry = get_object_or_404(get_scope(request).entries(), pk=pk)
    if request.method == "POST":
        if request.POST.get("expected_revision") != str(entry.revision):
            messages.error(request, "Операция изменилась или подтверждение устарело. Проверьте запись и откройте удаление заново.")
            return redirect("transaction_list")
        object_id, summary = entry.pk, f"{entry.description}: {entry.amount:.2f} ₽"
        try:
            entry.delete()
        except ProtectedError:
            messages.error(request, "Операция связана с проведённым платежом и сохраняется в истории.")
        else:
            record_event(get_scope(request), "deleted", "operation", object_id, summary)
            messages.success(request, "Операция удалена.")
        return redirect("transaction_list")
    return render(request, "cashflow/confirm_delete.html", {"instance": entry.description, "expected_revision": entry.revision, "cancel_url": "transaction_list"})


@login_required
def analytics(request):
    today = timezone.localdate()
    values = request.GET if request.GET else {"start": today.replace(day=1), "end": today}
    form = AnalysisPeriodForm(values)
    report = cashflow_analysis(request.user, scope=get_scope(request), **form.cleaned_data) if form.is_valid() else None
    return render(request, "cashflow/analytics.html", {"form": form, "report": report, "report_start": form.cleaned_data.get("start"), "report_end": form.cleaned_data.get("end")})
