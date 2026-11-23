from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import TestCase
from django.utils import timezone
from .access import LedgerScope
from .models import BankAccount, CashCategory, PlannedPayment
from .forecast import liquidity_scenario


class ForecastTests(TestCase):
    def test_dates_backlog_cancelled_exactness_and_account_isolation(self):
        user = get_user_model().objects.create_user("forecast")
        account = BankAccount.objects.create(owner=user, name="Счёт", opening_balance="100.10")
        expense = CashCategory.objects.create(owner=user, name="Расходы", kind="expense")
        today = timezone.localdate()
        for offset, status, amount in [(-2,"planned","10.10"),(2,"planned","100.00"),(1,"cancelled","999.00"),(31,"planned","999.00")]:
            PlannedPayment.objects.create(account=account, category=expense, due_on=today+timedelta(days=offset), status=status, amount=amount, description="План")
        report = liquidity_scenario(LedgerScope(user), account.pk, 30)
        self.assertEqual(report["opening"], Decimal("100.10"))
        self.assertEqual(report["overdue"], 1)
        self.assertEqual(report["rows"][0]["balance"], Decimal("90.00"))
        self.assertEqual(report["minimum"], Decimal("-10.00"))
        self.assertEqual(report["first_negative"], today+timedelta(days=2))
        other = get_user_model().objects.create_user("other-forecast")
        with self.assertRaises(Http404): liquidity_scenario(LedgerScope(other), account.pk, 30)
        with self.assertRaises(ValueError): liquidity_scenario(LedgerScope(user), account.pk, 91)
        self.client.force_login(user)
        self.assertContains(self.client.get("/forecast/", {"account": account.pk, "days": 30}), "Первый отрицательный остаток")
        self.assertEqual(PlannedPayment.objects.filter(status="planned").count(), 3)
