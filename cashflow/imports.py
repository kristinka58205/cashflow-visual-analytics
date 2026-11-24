"""CSV: проверка всего файла, просмотр и атомарное подтверждение импорта."""
import csv
import hashlib
import io
import json
from pathlib import Path
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .models import BankAccount, CashCategory, Counterparty, CashProject, CashTransaction, ImportBatch
from .access import LedgerScope, get_scope, financial_write, write_scope
from .audit import record_event

COLUMNS = ["date", "account", "category", "kind", "amount", "description", "counterparty", "project"]


class ImportForm(forms.Form):
    file = forms.FileField(label="CSV, UTF-8, разделитель ; (до 1 МиБ и 500 строк)")


class ConfirmImportForm(forms.Form):
    expected_preview = forms.CharField(min_length=64, max_length=64, widget=forms.HiddenInput)
    allow_repeats = forms.BooleanField(label="Подтверждаю повторяющиеся или совпадающие с журналом строки", required=False)


def parse_import(user, contents, scope=None):
    scope = scope or LedgerScope(user)
    if not contents or len(contents) > 1024*1024:
        raise ValidationError("Файл должен быть непустым и не превышать 1 МиБ.")
    try:
        reader = csv.DictReader(io.StringIO(contents.decode("utf-8-sig")), delimiter=";", strict=True)
        if reader.fieldnames != COLUMNS:
            raise ValidationError("Ожидаются колонки: " + ";".join(COLUMNS))
        accounts = {item.name: item.pk for item in scope.catalog(BankAccount)}
        categories = {(item.name, item.kind): item.pk for item in scope.catalog(CashCategory)}
        parties = {item.name: item.pk for item in scope.catalog(Counterparty)}
        projects = {item.name: item.pk for item in scope.catalog(CashProject)}
        rows = []
        for number, row in enumerate(reader, 2):
            if number > 501:
                raise ValidationError("Разрешено не более 500 операций за импорт.")
            if None in row or any(value is None for value in row.values()):
                raise ValidationError(f"Строка {number}: неверное число колонок.")
            try:
                values = {"account_id": accounts[row["account"]], "category_id": categories[(row["category"], row["kind"])],
                    "counterparty_id": parties[row["counterparty"]] if row["counterparty"] else None,
                    "project_id": projects[row["project"]] if row["project"] else None,
                    "occurred_on": row["date"], "amount": row["amount"], "description": row["description"]}
            except KeyError as exc:
                raise ValidationError(f"Строка {number}: неизвестный счёт, статья или необязательный справочник. Сначала создайте свою запись.") from exc
            entry = CashTransaction(**values)
            try:
                entry.full_clean()
            except ValidationError as exc:
                raise ValidationError(f"Строка {number}: " + "; ".join(exc.messages)) from exc
            values.update(occurred_on=entry.occurred_on.isoformat(), amount=f"{entry.amount:.2f}")
            rows.append(values)
        if not rows:
            raise ValidationError("В файле нет операций.")
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValidationError("Не удалось прочитать CSV UTF-8 с разделителем ;.") from exc


ROW_FIELDS={'account_id','category_id','counterparty_id','project_id','occurred_on','amount','description'}


def checked_rows(rows):
    if not isinstance(rows,list) or not 1<=len(rows)<=500:
        raise ValidationError('Повреждён предварительный просмотр. Загрузите исходный файл заново.')
    for row in rows:
        if not isinstance(row,dict) or set(row)!=ROW_FIELDS:
            raise ValidationError('Повреждён состав колонок импорта. Загрузите файл заново.')
        for field in ['account_id','category_id','counterparty_id','project_id']:
            value=row[field]
            if value is None and field in {'counterparty_id','project_id'}:continue
            if type(value) is not int or value<=0:raise ValidationError('Повреждены ссылки предварительного просмотра.')
        if any(not isinstance(row[field],str) for field in ['occurred_on','amount','description']):
            raise ValidationError('Повреждены значения предварительного просмотра.')
    return rows


def preview_fingerprint(scope,rows):
    checked_rows(rows)
    references={}
    for field,model,columns in [
        ('account_id',BankAccount,['pk','name']),('category_id',CashCategory,['pk','name','kind']),
        ('counterparty_id',Counterparty,['pk','name','tax_id']),('project_id',CashProject,['pk','name','code'])]:
        identifiers={row[field] for row in rows if row[field] is not None}
        values=list(scope.catalog(model).filter(pk__in=identifiers).order_by('pk').values(*columns))
        if len(values)!=len(identifiers):raise ValidationError('Справочник из предварительного просмотра больше недоступен. Загрузите файл заново.')
        references[field]=values
    content=json.dumps({'rows':rows,'references':references},sort_keys=True,ensure_ascii=False,allow_nan=False).encode()
    return hashlib.sha256(content).hexdigest()


def stage_import(user,contents,filename,scope=None):
    with write_scope(user,scope) as scope:
        rows=parse_import(user,contents,scope)
        fingerprint=preview_fingerprint(scope,rows)
        batch,created=scope.imports().get_or_create(sha256=hashlib.sha256(contents).hexdigest(),
            defaults={'owner':user,'organization':scope.organization,'filename':Path(filename).name[:120],
                      'rows':rows,'preview_sha256':fingerprint})
        if not batch.applied_at and batch.preview_sha256!=fingerprint:
            batch.rows=rows;batch.preview_sha256=fingerprint
            batch.save(update_fields=['rows','preview_sha256'])
        if created:record_event(scope,'created','import',batch.pk,f'{batch.filename}: предварительная проверка {len(rows)} строк')
        return batch


def repeated_rows(rows):
    seen, repeats = set(), 0
    for row in rows:
        key = tuple(sorted(row.items()))
        if key in seen or CashTransaction.objects.filter(**row).exists():
            repeats += 1
        seen.add(key)
    return repeats


@transaction.atomic
def apply_import(user, pk, allow_repeats=False, scope=None, expected_preview=None):
    with write_scope(user, scope) as scope:
        batch = get_object_or_404(scope.imports().select_for_update(), pk=pk)
        checked_rows(batch.rows)
        if batch.applied_at:
            return len(batch.rows)
        if not batch.preview_sha256 or expected_preview!=batch.preview_sha256 or preview_fingerprint(scope,batch.rows)!=batch.preview_sha256:
            raise ValidationError("Предварительный просмотр устарел: данные или справочники изменились. Загрузите исходный файл заново и проверьте новый просмотр.")
        if repeated_rows(batch.rows) and not allow_repeats:
            raise ValidationError("Есть повторяющиеся операции. Проверьте их и подтвердите отдельным флажком.")
        for row in batch.rows:
            if not scope.catalog(BankAccount).filter(pk=row["account_id"]).exists():
                raise ValidationError("Счёт из предварительного просмотра больше недоступен. Загрузите исправленный файл.")
            entry = CashTransaction(**row)
            entry.full_clean()
            entry.save()
        batch.applied_at = timezone.now()
        batch.save(update_fields=["applied_at"])
        record_event(scope, "imported", "import", batch.pk, f"{batch.filename}: {len(batch.rows)} операций")
        return len(batch.rows)


@login_required
@financial_write
def import_upload(request):
    form = ImportForm(request.POST if request.method == "POST" else None, request.FILES if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        contents = upload.read(1024*1024+1)
        try:
            batch = stage_import(request.user, contents, upload.name, scope=get_scope(request))
        except ValidationError as exc:
            form.add_error("file", exc)
        else:
            return redirect("import_preview", pk=batch.pk)
    batches = get_scope(request).imports().order_by("-pk")[:10]
    return render(request, "cashflow/import_upload.html", {"form": form, "batches": batches})


@login_required
@financial_write
def import_preview(request, pk):
    batch = get_object_or_404(get_scope(request).imports(), pk=pk)
    form = ConfirmImportForm(request.POST if request.method == "POST" else None, initial={"expected_preview":batch.preview_sha256})
    if request.method == "POST" and form.is_valid():
        try:
            count = apply_import(request.user, pk, scope=get_scope(request), **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, f"Импорт выполнен однократно: {count} операций.")
            return redirect("transaction_list")
    try:
        checked_rows(batch.rows)
        if not batch.applied_at and (not batch.preview_sha256 or preview_fingerprint(get_scope(request),batch.rows)!=batch.preview_sha256):
            raise ValidationError('Предварительный просмотр устарел. Загрузите исходный файл заново.')
    except ValidationError as exc:
        return render(request,"cashflow/import_preview.html",{"batch":batch,"form":None,"import_error":" ".join(exc.messages),"preview":[],"repeats":0},status=409)
    accounts = dict(get_scope(request).catalog(BankAccount).values_list("pk", "name"))
    categories = dict(get_scope(request).catalog(CashCategory).values_list("pk", "name"))
    preview = [{**row, "account_name": accounts.get(row["account_id"], "Удалённый счёт"),
        "category_name": categories.get(row["category_id"], "Удалённая статья")} for row in batch.rows]
    return render(request, "cashflow/import_preview.html", {"batch": batch, "form": form, "preview": preview,
        "repeats": repeated_rows(batch.rows) if not batch.applied_at else 0})
