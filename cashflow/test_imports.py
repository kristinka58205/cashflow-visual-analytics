from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import BankAccount, CashCategory, CashTransaction, ImportBatch
from .imports import parse_import, apply_import, preview_fingerprint, stage_import
from .access import LedgerScope


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
        self.client.post(url, {"expected_preview":batch.preview_sha256})
        self.client.post(url, {"expected_preview":batch.preview_sha256})
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
        batch = ImportBatch.objects.create(owner=self.user, filename="repeat.csv", sha256="a"*64, rows=rows+rows, preview_sha256=preview_fingerprint(LedgerScope(self.user),rows+rows))
        with self.assertRaises(ValidationError):
            apply_import(self.user, batch.pk, expected_preview=batch.preview_sha256)
        self.assertFalse(CashTransaction.objects.exists())
        apply_import(self.user, batch.pk, True, expected_preview=batch.preview_sha256)
        self.assertEqual(CashTransaction.objects.count(), 2)

    def test_changed_references_roll_back_all_operations(self):
        rows = parse_import(self.user, self.payload)
        batch = ImportBatch.objects.create(owner=self.user, filename="changed.csv", sha256="b"*64,
            rows=rows+[{**rows[0], "category_id": 999999, "description": "Different"}])
        with self.assertRaises(ValidationError):
            apply_import(self.user, batch.pk, expected_preview=batch.preview_sha256)
        self.assertFalse(CashTransaction.objects.exists())
        batch.refresh_from_db();self.assertIsNone(batch.applied_at)

    def test_changed_category_after_preview_requires_new_upload_and_old_tab_is_stale(self):
        batch=stage_import(self.user,self.payload,'bank.csv');old=batch.preview_sha256
        # This category is still unused, so changing its direction is permitted.
        self.category.kind='expense';self.category.save()
        with self.assertRaises(ValidationError):apply_import(self.user,batch.pk,expected_preview=old)
        self.assertFalse(CashTransaction.objects.exists())
        self.assertEqual(self.client.get(reverse('import_preview',args=[batch.pk])).status_code,409)
        self.category.kind='income';self.category.name='Sales';self.category.save()
        self.account.name='Renamed';self.account.save()
        self.assertEqual(self.client.get(reverse('import_preview',args=[batch.pk])).status_code,409)
        # A fresh explicit upload can refresh the snapshot, including a legacy batch.
        self.account.name='Main';self.account.save()
        batch.preview_sha256='';batch.save(update_fields=['preview_sha256'])
        renewed=stage_import(self.user,self.payload,'bank.csv')
        self.assertEqual(renewed.pk,batch.pk)
        apply_import(self.user,batch.pk,expected_preview=renewed.preview_sha256)
        self.assertEqual(CashTransaction.objects.count(),1)

    def test_corrupt_rows_fail_closed_without_partial_apply_or_preview_exception(self):
        batch=stage_import(self.user,self.payload,'bank.csv')
        for rows in [[], 'damaged',[{'unknown':1}],[{**batch.rows[0],'account_id':True}]]:
            ImportBatch.objects.filter(pk=batch.pk).update(rows=rows)
            with self.assertRaises(ValidationError):apply_import(self.user,batch.pk,expected_preview=batch.preview_sha256)
            self.assertEqual(self.client.get(reverse('import_preview',args=[batch.pk])).status_code,409)
            self.assertFalse(CashTransaction.objects.exists())

    def test_old_tab_cannot_confirm_refreshed_mapping_of_same_file(self):
        batch=stage_import(self.user,self.payload,'bank.csv');old=batch.preview_sha256
        self.category.name='Old name';self.category.save()
        replacement=CashCategory.objects.create(owner=self.user,name='Sales',kind='income')
        renewed=stage_import(self.user,self.payload,'bank.csv')
        self.assertNotEqual(old,renewed.preview_sha256)
        with self.assertRaises(ValidationError):apply_import(self.user,batch.pk,expected_preview=old)
        self.assertFalse(CashTransaction.objects.exists())
        apply_import(self.user,batch.pk,expected_preview=renewed.preview_sha256)
        self.assertEqual(CashTransaction.objects.get().category,replacement)
