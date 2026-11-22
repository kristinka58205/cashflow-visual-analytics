from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import BankAccount, CashCategory, CashTransaction
from .services import financial_summary
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal


class RegistrationTests(TestCase):
    def test_register_and_log_in(self):
        response = self.client.post(reverse("register"), {
            "username": "analyst", "password1": "Finance-Review-2048!", "password2": "Finance-Review-2048!",
        })
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), get_user_model().objects.get(username="analyst").pk)

    def test_public_home_has_author(self):
        self.assertContains(self.client.get(reverse("home")), "Жукова Кристина Дмитриевна")


class CatalogTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("analyst")
        self.other = get_user_model().objects.create_user("other")
        self.client.force_login(self.user)

    def test_account_owner_cannot_be_spoofed(self):
        self.client.post(reverse("catalog_create", args=["accounts"]), {"name": "Основной", "opening_balance": "123.45", "owner": self.other.pk})
        self.assertEqual(BankAccount.objects.get().owner, self.user)

    def test_duplicate_names_rejected(self):
        url = reverse("catalog_create", args=["accounts"])
        self.client.post(url, {"name": "Основной", "opening_balance": "0"})
        self.assertContains(self.client.post(url, {"name": "Основной", "opening_balance": "0"}), "уже используется")
        self.assertEqual(BankAccount.objects.count(), 1)

    def test_foreign_data_cannot_be_changed(self):
        account = BankAccount.objects.create(owner=self.other, name="Чужой")
        self.assertNotContains(self.client.get(reverse("catalog")), "Чужой")
        for action in ["catalog_edit", "catalog_delete"]:
            self.assertEqual(self.client.post(reverse(action, args=["accounts", account.pk]), {}).status_code, 404)

    def test_deletion_requires_confirmation(self):
        category = CashCategory.objects.create(owner=self.user, name="Продажи", kind="income")
        url = reverse("catalog_delete", args=["categories", category.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(CashCategory.objects.exists())
        self.client.post(url)
        self.assertFalse(CashCategory.objects.exists())


class TransactionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("analyst")
        self.other = get_user_model().objects.create_user("other")
        self.account = BankAccount.objects.create(owner=self.user, name="Основной", opening_balance="100.10")
        self.income = CashCategory.objects.create(owner=self.user, name="Продажи", kind="income")
        self.expense = CashCategory.objects.create(owner=self.user, name="Расходы", kind="expense")
        self.client.force_login(self.user)

    def post_entry(self, **overrides):
        values = {"account": self.account.pk, "category": self.income.pk, "occurred_on": timezone.localdate(), "amount": "0.10", "description": "Платёж"}
        values.update(overrides)
        return self.client.post(reverse("transaction_create"), values)

    def test_summary_uses_exact_decimal_and_prior_balance(self):
        today = timezone.localdate()
        for date, amount, category in [(today-timedelta(days=2), "20.10", self.income), (today, "0.10", self.income), (today, "0.20", self.expense)]:
            CashTransaction.objects.create(account=self.account, category=category, occurred_on=date, amount=amount, description="Тест")
        result = financial_summary(self.user, today, today)
        self.assertEqual(result["opening"], Decimal("120.20"))
        self.assertEqual(result["net"], Decimal("-0.10"))
        self.assertEqual(result["closing"], Decimal("120.10"))

    def test_foreign_category_cannot_be_selected(self):
        foreign = CashCategory.objects.create(owner=self.other, name="Чужая", kind="income")
        self.post_entry(category=foreign.pk)
        self.assertFalse(CashTransaction.objects.exists())
        with self.assertRaises(ValidationError):
            CashTransaction(account=self.account, category=foreign, occurred_on=timezone.localdate(), amount="10", description="Тест").full_clean()

    def test_invalid_amounts_and_future_actual_dates_rejected(self):
        for amount in ["0", "-1", "NaN", "1.001"]:
            self.post_entry(amount=amount)
        self.post_entry(occurred_on=timezone.localdate()+timedelta(days=1))
        self.assertFalse(CashTransaction.objects.exists())

    def test_category_direction_and_references_protected(self):
        self.post_entry()
        response = self.client.post(reverse("catalog_edit", args=["categories", self.income.pk]), {"name": "Продажи", "kind": "expense"})
        self.assertContains(response, "менять нельзя")
        self.client.post(reverse("catalog_delete", args=["accounts", self.account.pk]))
        self.assertTrue(BankAccount.objects.filter(pk=self.account.pk).exists())

    def test_foreign_entries_are_inaccessible(self):
        self.post_entry()
        entry = CashTransaction.objects.get()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("transaction_list")).context["page"].paginator.count, 0)
        for action in ["transaction_edit", "transaction_delete"]:
            self.assertEqual(self.client.post(reverse(action, args=[entry.pk]), {}).status_code, 404)
        self.assertEqual(financial_summary(self.other)["income"], Decimal("0.00"))
