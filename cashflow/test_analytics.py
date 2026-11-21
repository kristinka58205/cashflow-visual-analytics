from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import BankAccount, CashCategory, CashTransaction
from .services import cashflow_analysis


class AnalyticsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("analyst-report")
        self.client.force_login(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name="Рублёвый", opening_balance="100.10")
        self.income = CashCategory.objects.create(owner=self.user, name="Выручка", kind="income")
        self.expense = CashCategory.objects.create(owner=self.user, name="Аренда", kind="expense")

    def test_partial_months_missing_month_and_opening_balance(self):
        for day, amount, category in [(date(2025, 12, 1), "20.10", self.income),
                (date(2025, 12, 20), "30.30", self.income), (date(2026, 2, 5), "10.10", self.expense)]:
            CashTransaction.objects.create(account=self.account, category=category, amount=amount, occurred_on=day, description="Тест")
        report = cashflow_analysis(self.user, date(2025, 12, 15), date(2026, 2, 10))
        self.assertEqual(len(report["months"]), 3)
        self.assertEqual(report["months"][1]["net"], Decimal("0.00"))
        self.assertEqual(report["summary"]["opening"], Decimal("120.20"))
        self.assertEqual(report["months"][-1]["closing"], Decimal("140.40"))
        self.assertEqual(report["categories"][0]["share"], Decimal("100"))

    def test_empty_and_foreign_data_have_no_chart_values(self):
        other = get_user_model().objects.create_user("foreign-analyst")
        other_account = BankAccount.objects.create(owner=other, name="Чужой")
        other_category = CashCategory.objects.create(owner=other, name="Секрет", kind="income")
        CashTransaction.objects.create(account=other_account, category=other_category, amount="999", occurred_on=date(2026, 1, 1), description="Тест")
        report = cashflow_analysis(self.user, date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual(report["categories"], [])
        self.assertEqual(report["months"][0]["income_width"], "0")
        self.assertEqual(report["summary"]["closing"], Decimal("100.10"))

    def test_period_validation_and_page_rendering(self):
        for params in [{"start": "2026-02-02", "end": "2026-01-01"},
                       {"start": "2020-01-01", "end": "2026-01-01"}, {"start": "bad"}]:
            response = self.client.get(reverse("analytics"), params)
            self.assertTrue(response.context["form"].errors)
            self.assertIsNone(response.context["report"])
        self.assertContains(self.client.get(reverse("analytics")), "Аналитика потоков")
        self.assertEqual(self.client.get(reverse("analytics"), {"start": "9999-12-01", "end": "9999-12-31"}).status_code, 200)
