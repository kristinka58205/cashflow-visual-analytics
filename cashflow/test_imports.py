from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import BankAccount, CashCategory, CashTransaction, ImportBatch
from .imports import parse_import, apply_import


class ImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("import-user")
        self.other = get_user_model().objects.create_user("import-other")
        self.client.force_login(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name="Main")
        self.category = CashCategory.objects.create(owner=self.user, name="Sales", kind="income")
        self.payload = ("date;account;category;kind;amount;description;counterparty;project\n"
            +f"{timezone.localdate()};Main;Sales;income;10.10;Invoice;;\n").encode()

    def test_upload_preview_confirm_and_retry_are_idempotent(self):
        self.client.post(reverse("import_upload"), {"file": SimpleUploadedFile("bank.csv", self.payload)})
        batch = ImportBatch.objects.get()
        self.assertFalse(CashTransaction.objects.exists())
        url = reverse("import_preview", args=[batch.pk])
        self.assertContains(self.client.get(url), "Invoice")
        self.client.post(url, {})
        self.client.post(url, {})
        self.assertEqual(CashTransaction.objects.count(), 1)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_bad_file_or_foreign_reference_creates_no_rows(self):
        for payload in [self.payload.replace(b"10.10", b"-1"), self.payload.replace(b"Main", b"Foreign"), b"bad,header\n"]:
            with self.assertRaises(ValidationError):
                parse_import(self.user, payload)
        self.assertFalse(CashTransaction.objects.exists())

    def test_duplicates_require_explicit_checkbox(self):
        rows = parse_import(self.user, self.payload)
        batch = ImportBatch.objects.create(owner=self.user, filename="repeat.csv", sha256="a"*64, rows=rows+rows)
        with self.assertRaises(ValidationError):
            apply_import(self.user, batch.pk)
        self.assertFalse(CashTransaction.objects.exists())
        apply_import(self.user, batch.pk, True)
        self.assertEqual(CashTransaction.objects.count(), 2)

    def test_changed_references_roll_back_all_operations(self):
        rows = parse_import(self.user, self.payload)
        batch = ImportBatch.objects.create(owner=self.user, filename="changed.csv", sha256="b"*64,
            rows=rows+[{**rows[0], "category_id": 999999, "description": "Different"}])
        with self.assertRaises(ValidationError):
            apply_import(self.user, batch.pk)
        self.assertFalse(CashTransaction.objects.exists())
        batch.refresh_from_db();self.assertIsNone(batch.applied_at)
