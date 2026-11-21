from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import BankAccount, CashCategory, CashTransaction, ExpenseBudget
from .budgets import budget_overview


class BudgetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("budget-user")
        self.other = get_user_model().objects.create_user("budget-other")
        self.client.force_login(self.user)
        self.category = CashCategory.objects.create(owner=self.user, name="Аренда", kind="expense")

    def test_budget_form_uniqueness_and_expense_only(self):
        payload = {"category": self.category.pk, "month_start": "2026-11", "limit": "100.10"}
        self.client.post(reverse("budget_create"), payload)
        self.client.post(reverse("budget_create"), payload)
        self.assertEqual(ExpenseBudget.objects.count(), 1)
        income = CashCategory.objects.create(owner=self.user, name="Выручка", kind="income")
        self.client.post(reverse("budget_create"), {**payload, "category": income.pk})
        self.assertEqual(ExpenseBudget.objects.count(), 1)

    def test_exact_plan_actual_and_access(self):
        budget = ExpenseBudget.objects.create(owner=self.user, category=self.category, month_start=date(2026, 11, 1), limit="100.10")
        account = BankAccount.objects.create(owner=self.user, name="Счёт")
        CashTransaction.objects.create(account=account, category=self.category, amount="100.11", occurred_on=date(2026, 11, 1), description="Аренда")
        row = budget_overview(self.user, date(2026, 11, 1))[0]
        self.assertEqual(row["remaining"], Decimal("-0.01"))
        self.assertTrue(row["exceeded"])
        self.assertEqual(self.client.get(reverse("budget_list"), {"month": "bad"}).status_code, 200)
        self.client.post(reverse("catalog_edit", args=["categories", self.category.pk]), {"name": "Аренда", "kind": "income"})
        self.category.refresh_from_db();self.assertEqual(self.category.kind, "expense")
        self.client.force_login(self.other)
        self.assertEqual(budget_overview(self.other, date(2026, 11, 1)), [])
        self.assertEqual(self.client.post(reverse("budget_delete", args=[budget.pk])).status_code, 404)
