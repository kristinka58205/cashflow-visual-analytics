import csv
import io
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import BankAccount, CashCategory, CashTransaction


class ExportTests(TestCase):
    def test_export_uses_filters_owner_and_safe_text_cells(self):
        user = get_user_model().objects.create_user("exporter")
        other = get_user_model().objects.create_user("other-exporter")
        for owner in [user, other]:
            account = BankAccount.objects.create(owner=owner, name="Основной")
            income = CashCategory.objects.create(owner=owner, name="Продажи", kind="income")
            CashTransaction.objects.create(account=account, category=income, amount="12.34", description="=1+1")
            CashTransaction.objects.create(account=account, category=income, amount="99.00", description="Другой")
        self.client.force_login(user)
        response = self.client.get(reverse("transaction_export"), {"maximum": "20"})
        rows = list(csv.DictReader(io.StringIO(b"".join(response.streaming_content).decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["amount"], rows[0]["description"]), ("12.34", "'=1+1"))
        self.assertEqual(self.client.get(reverse("transaction_export"), {"minimum": "bad"}).status_code, 400)

    def test_anonymous_export_requires_login(self):
        self.assertEqual(self.client.get(reverse("transaction_export")).status_code, 302)
