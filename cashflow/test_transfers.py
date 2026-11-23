from decimal import Decimal
import uuid
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import TestCase
from django.utils import timezone
from .access import LedgerScope
from .models import BankAccount, CashTransfer, LedgerEvent
from .reconciliation import balance_at
from .services import financial_summary
from .transfers import create_transfer


class TransferTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('transfer')
        self.scope=LedgerScope(self.user)
        self.first=BankAccount.objects.create(owner=self.user,name='Первый',opening_balance='.30')
        self.second=BankAccount.objects.create(owner=self.user,name='Второй')
        self.today=timezone.localdate()
        self.values=dict(from_account=self.first,to_account=self.second,amount=Decimal('.20'),occurred_on=self.today,description='Свой перевод',command_key=uuid.uuid4())

    def test_balances_external_flow_and_exact_repeat(self):
        item,created=create_transfer(self.user,self.scope,**self.values)
        self.assertTrue(created)
        self.assertEqual(balance_at(self.scope,self.first,self.today),Decimal('.10'))
        self.assertEqual(balance_at(self.scope,self.second,self.today),Decimal('.20'))
        summary=financial_summary(self.user,scope=self.scope)
        self.assertEqual((summary['income'],summary['expense'],summary['closing']),(Decimal(0),Decimal(0),Decimal('.30')))
        self.assertEqual(create_transfer(self.user,self.scope,**self.values),(item,False))
        self.assertEqual(LedgerEvent.objects.filter(entity='transfer').count(),1)
        with self.assertRaises(ValidationError):create_transfer(self.user,self.scope,**{**self.values,'amount':Decimal('.21')})

    def test_foreign_account_and_audit_failure_leave_no_transfer(self):
        other=get_user_model().objects.create_user('foreign-transfer')
        foreign=BankAccount.objects.create(owner=other,name='Чужой')
        with self.assertRaises(Http404):create_transfer(self.user,self.scope,**{**self.values,'to_account':foreign})
        with patch('cashflow.transfers.record_event',side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):create_transfer(self.user,self.scope,**self.values)
        self.assertFalse(CashTransfer.objects.exists())
