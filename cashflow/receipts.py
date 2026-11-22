import hashlib
from io import BytesIO
from pathlib import Path
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_POST
from .access import financial_write, get_scope
from .audit import record_event
from .models import TransactionReceipt

MAX_FILE = 1024 * 1024
MAX_TOTAL = 10 * MAX_FILE


class ReceiptForm(forms.Form):
    file = forms.FileField(label="Документ PDF или TXT (до 1 МиБ)")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not 0 < upload.size <= MAX_FILE:
            raise ValidationError("Допустим непустой файл до 1 МиБ.")
        data = upload.read(MAX_FILE + 1)
        if len(data) != upload.size or len(data) > MAX_FILE:
            raise ValidationError("Некорректный размер файла.")
        suffix = Path(upload.name).suffix.lower()
        if suffix == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise ValidationError("Файл не имеет заголовка PDF.")
        elif suffix == ".txt":
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValidationError("TXT должен быть в UTF-8.") from exc
            if "\x00" in text:
                raise ValidationError("TXT содержит двоичные данные.")
        else:
            raise ValidationError("Допускаются только PDF и TXT.")
        try:
            filename = get_valid_filename(Path(upload.name).name)[:120]
        except Exception as exc:
            raise ValidationError("Недопустимое имя файла.") from exc
        return {"filename": filename, "content": data, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


@login_required
def receipt_list(request, pk):
    entry = get_object_or_404(get_scope(request).entries(), pk=pk)
    return render(request, "cashflow/receipts.html", {"entry": entry, "receipts": entry.receipts.defer("content").order_by("pk"), "form": ReceiptForm()})


@login_required
@require_POST
@financial_write
def receipt_upload(request, pk):
    scope = get_scope(request)
    entry = get_object_or_404(scope.entries(), pk=pk)
    form = ReceiptForm(request.POST, request.FILES)
    if form.is_valid():
        data = form.cleaned_data["file"]
        total = TransactionReceipt.objects.filter(transaction__in=scope.entries()).aggregate(total=Sum("size"))["total"] or 0
        if entry.receipts.filter(sha256=data["sha256"]).exists():
            form.add_error("file", "Этот документ уже прикреплён к операции.")
        elif total + data["size"] > MAX_TOTAL:
            form.add_error("file", "Суммарный объём документов области ограничен 10 МиБ.")
        else:
            receipt = TransactionReceipt.objects.create(transaction=entry, uploaded_by=request.user, **data)
            record_event(scope, "created", "receipt", receipt.pk, f"Документ операции {entry.pk}: {receipt.filename}")
            return redirect("receipt_list", pk=pk)
    return render(request, "cashflow/receipts.html", {"entry": entry, "receipts": entry.receipts.defer("content"), "form": form}, status=400)


@login_required
def receipt_download(request, pk):
    receipt = get_object_or_404(TransactionReceipt.objects.filter(transaction__in=get_scope(request).entries()), pk=pk)
    response = FileResponse(BytesIO(bytes(receipt.content)), as_attachment=True, filename=receipt.filename, content_type="application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
@financial_write
def receipt_delete(request, pk):
    scope = get_scope(request)
    receipt = get_object_or_404(TransactionReceipt.objects.filter(transaction__in=scope.entries()), pk=pk)
    entry_id = receipt.transaction_id
    record_event(scope, "deleted", "receipt", receipt.pk, f"Удалён документ операции {entry_id}: {receipt.filename}")
    receipt.delete()
    messages.success(request, "Документ удалён.")
    return redirect("receipt_list", pk=entry_id)
