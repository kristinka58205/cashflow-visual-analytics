from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.core.exceptions import PermissionDenied
from .access import LedgerScope
from .operations import create_operation
from django.urls import reverse
from django.utils import timezone
from .models import BankAccount, CashCategory, CashTransaction, LedgerEvent, OperationCommand


class OperationCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('manual-command')
        self.client.force_login(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name='Main')
        self.category = CashCategory.objects.create(owner=self.user, name='Sales', kind='income')
        self.url = reverse('transaction_create')
        self.key = self.client.get(self.url).context['form']['command_key'].value()
        self.values = dict(command_key=self.key, account=self.account.pk, category=self.category.pk,
                           occurred_on=timezone.localdate(), amount='0.10', description='First operation')

    def test_exact_repeat_records_one_operation_one_event(self):
        for _ in range(2):
            self.assertRedirects(self.client.post(self.url, self.values), reverse('transaction_list'))
        self.assertEqual(CashTransaction.objects.count(), 1)
        self.assertEqual(LedgerEvent.objects.filter(entity='operation').count(), 1)
        self.assertEqual(OperationCommand.objects.count(), 1)
        self.assertContains(self.client.post(self.url, {**self.values, 'amount': '0.20'}), 'другими данными')
        self.assertEqual(CashTransaction.objects.get().amount.as_tuple().digits, (1, 0))

    def test_deleted_operation_cannot_be_recreated_by_old_post(self):
        self.client.post(self.url, self.values)
        entry = CashTransaction.objects.get()
        self.client.post(reverse('transaction_delete', args=[entry.pk]))
        self.assertContains(self.client.post(self.url, self.values), 'затем удалена')
        self.assertFalse(CashTransaction.objects.exists())
        self.assertIsNone(OperationCommand.objects.get().entry_id)

    def test_missing_key_and_foreign_replay_do_not_write(self):
        self.assertContains(self.client.post(self.url, {**self.values, 'command_key': ''}), 'Обязательное поле')
        self.assertFalse(CashTransaction.objects.exists())
        self.client.post(self.url, self.values)
        other = get_user_model().objects.create_user('foreign-command')
        other_account = BankAccount.objects.create(owner=other, name='Other')
        other_category = CashCategory.objects.create(owner=other, name='Other', kind='income')
        self.client.force_login(other)
        self.assertContains(self.client.post(self.url, {**self.values, 'account': other_account.pk,
                            'category': other_category.pk}), 'другими данными')
        self.assertEqual(CashTransaction.objects.count(), 1)

    def test_audit_failure_rolls_back_receipt_and_money(self):
        with patch('cashflow.audit.LedgerEvent.objects.create', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url, self.values)
        self.assertFalse(CashTransaction.objects.exists())
        self.assertFalse(OperationCommand.objects.exists())
        self.assertRedirects(self.client.post(self.url, self.values), reverse('transaction_list'))

    def test_stale_user_object_cannot_write_after_deactivation(self):
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        entry = CashTransaction(account=self.account, category=self.category, occurred_on=timezone.localdate(),
                                amount=__import__('decimal').Decimal('0.10'), description='Denied')
        with self.assertRaises(PermissionDenied):
            create_operation(self.user, LedgerScope(self.user), uuid4(), entry)
        self.assertFalse(CashTransaction.objects.exists())
