from datetime import date
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from .access import LedgerScope
from .positions import account_positions
from .models import BankAccount,CashCategory,CashTransaction,CashTransfer,AccountReconciliation
from .reconciliation import balance_at
from .services import financial_summary


class PositionTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('positions');self.scope=LedgerScope(self.user)
        self.first=BankAccount.objects.create(owner=self.user,name='First',opening_balance='.10')
        self.second=BankAccount.objects.create(owner=self.user,name='Second')
        self.category=CashCategory.objects.create(owner=self.user,name='Income',kind='income')
        self.day=date(2026,1,2)

    def test_aggregate_matches_ledger_and_internal_transfers_cancel(self):
        CashTransaction.objects.create(account=self.first,category=self.category,occurred_on=self.day,amount='.20',description='Income')
        CashTransfer.objects.create(owner=self.user,from_account=self.first,to_account=self.second,occurred_on=self.day,amount='.40',description='Internal',command_key=uuid4())
        report=account_positions(self.user,self.scope,self.day)
        self.assertEqual([row['balance'] for row in report['rows']],[Decimal('-.10'),Decimal('.40')])
        for row in report['rows']:self.assertEqual(row['balance'],balance_at(self.scope,row['account'],self.day))
        self.assertEqual(report['totals']['balance'],financial_summary(self.user,end=self.day)['closing'])
        self.assertEqual(report['totals']['transfer_in'],report['totals']['transfer_out'])
        self.assertEqual(report['negative_count'],1)

    def test_latest_same_day_snapshot_is_read_only_and_stale_is_visible(self):
        old=AccountReconciliation.objects.create(account=self.first,checked_on=self.day,statement_balance='.10',ledger_balance='.10',actor=self.user)
        CashTransaction.objects.create(account=self.first,category=self.category,occurred_on=self.day,amount='.20',description='Later recorded')
        row=account_positions(self.user,self.scope,self.day)['rows'][0]
        self.assertTrue(row['changed_after_snapshot']);self.assertEqual(row['difference'],Decimal('-.20'))
        old.refresh_from_db();self.assertEqual(old.ledger_balance,Decimal('.10'))
        self.client.force_login(self.user)
        response=self.client.get('/balances/',{'day':self.day,'format':'csv'})
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertIn('0.30',response.content.decode('utf-8-sig'))
        self.assertContains(self.client.get('/balances/',{'day':self.day}),'Учёт изменился после сверки')

    def test_scope_and_boundaries(self):
        other=get_user_model().objects.create_user('positions-other')
        BankAccount.objects.create(owner=other,name='Secret',opening_balance='999')
        self.assertEqual(account_positions(self.user,self.scope,self.day)['totals']['balance'],Decimal('.10'))
        with self.assertRaises(PermissionDenied):account_positions(other,self.scope,self.day)
        with patch('cashflow.positions.MAX_ACCOUNTS',0),self.assertRaises(ValidationError):account_positions(self.user,self.scope,self.day)
        with self.assertRaises(ValidationError):account_positions(self.user,self.scope,date(1,1,1))
