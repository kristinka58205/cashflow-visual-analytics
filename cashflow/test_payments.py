from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import BankAccount, CashCategory, CashTransaction, PlannedPayment
from .payments import execute_payment, save_payment


class PaymentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("payment-user")
        self.client.force_login(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name="Счёт")
        self.category = CashCategory.objects.create(owner=self.user, name="Расход", kind="expense")
        self.today = timezone.localdate()
        self.payment = PlannedPayment.objects.create(account=self.account, category=self.category, due_on=self.today+timedelta(days=3), amount="100.10", description="Аренда")

    def test_execution_is_atomic_and_idempotent(self):
        first = execute_payment(self.user, self.payment.pk, self.today)
        second = execute_payment(self.user, self.payment.pk, self.today)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CashTransaction.objects.count(), 1)
        self.client.post(reverse("transaction_delete", args=[first.pk]))
        self.assertTrue(CashTransaction.objects.filter(pk=first.pk).exists())
        with self.assertRaises(ValidationError):
            save_payment(self.user, self.payment)
        self.assertContains(self.client.get(reverse("payment_list"), {"state": "paid"}), "Аренда")

    def test_future_actual_and_cancelled_payments_are_rejected(self):
        with self.assertRaises(ValidationError):
            execute_payment(self.user, self.payment.pk, self.today+timedelta(days=1))
        self.payment.refresh_from_db();self.assertEqual(self.payment.status, "planned")
        self.client.post(reverse("payment_cancel", args=[self.payment.pk]))
        with self.assertRaises(ValidationError):
            execute_payment(self.user, self.payment.pk, self.today)
        self.assertFalse(CashTransaction.objects.exists())

    def test_foreign_access_and_plan_form(self):
        self.assertContains(self.client.get(reverse("payment_create")), "Плановый платёж")
        self.client.post(reverse("payment_create"), {"account": self.account.pk, "category": self.category.pk,
            "due_on": self.today, "amount": "2.10", "description": "Новый"})
        self.assertEqual(PlannedPayment.objects.count(), 2)
        other = get_user_model().objects.create_user("payment-other")
        self.client.force_login(other)
        for action in ["payment_edit", "payment_execute", "payment_cancel"]:
            self.assertEqual(self.client.post(reverse(action, args=[self.payment.pk]), {}).status_code, 404)
