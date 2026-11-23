from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from .demo import seed_demo
from .models import BankAccount, CashTransaction, DemoDataset, LedgerEvent


class DemoTests(TestCase):
    def test_repetition_and_isolation(self):
        user = get_user_model().objects.create_user("demo")
        other = get_user_model().objects.create_user("other")
        untouched = BankAccount.objects.create(owner=other, name="Личный")
        marker, created = seed_demo(user)
        self.assertTrue(created)
        self.assertEqual(seed_demo(user), (marker, False))
        self.assertEqual(CashTransaction.objects.count(), 20)
        self.assertEqual(LedgerEvent.objects.count(), 22)
        untouched.refresh_from_db()
        self.assertEqual(untouched.name, "Личный")

    def test_existing_data_and_failure_leave_no_partial_demo(self):
        user = get_user_model().objects.create_user("existing")
        BankAccount.objects.create(owner=user, name="Мой счёт")
        with self.assertRaises(ValidationError): seed_demo(user)
        empty = get_user_model().objects.create_user("empty")
        with patch("cashflow.demo.record_event", side_effect=RuntimeError("audit")):
            with self.assertRaises(RuntimeError): seed_demo(empty)
        self.assertFalse(BankAccount.objects.filter(owner=empty).exists())
        self.assertFalse(DemoDataset.objects.exists())
