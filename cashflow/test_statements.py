from datetime import date
from decimal import Decimal
import uuid
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import TestCase
from django.core.exceptions import ValidationError
from .access import LedgerScope
from .models import BankAccount,CashCategory,CashTransaction,CashTransfer
from .statements import account_statement
from .reconciliation import balance_at


class StatementTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('statement');self.scope=LedgerScope(self.user)
        self.account=BankAccount.objects.create(owner=self.user,name='Первый',opening_balance='0.30')
        self.second=BankAccount.objects.create(owner=self.user,name='Второй')
        self.category=CashCategory.objects.create(owner=self.user,name='Доход',kind='income')

    def test_opening_transfers_running_balance_and_empty_period(self):
        for day,amount in [(1,'.10'),(2,'.20')]:CashTransaction.objects.create(account=self.account,category=self.category,occurred_on=date(2026,1,day),amount=amount,description='Доход')
        CashTransfer.objects.create(owner=self.user,from_account=self.account,to_account=self.second,amount='.15',occurred_on=date(2026,1,2),description='Свой перевод',command_key=uuid.uuid4())
        report=account_statement(self.user,self.scope,self.account,date(2026,1,2),date(2026,1,2))
        self.assertEqual((report['opening'],report['credits'],report['debits'],report['closing']),(Decimal('.40'),Decimal('.20'),Decimal('.15'),Decimal('.45')))
        self.assertEqual(report['rows'][-1]['balance'],balance_at(self.scope,self.account,date(2026,1,2)))
        self.assertEqual([r['source'] for r in report['rows']],['operation','transfer'])
        empty=account_statement(self.user,self.scope,self.account,date(2026,1,3),date(2026,1,3))
        self.assertEqual(empty['opening'],empty['closing']);self.assertEqual(empty['rows'],[])

    def test_foreign_account_and_over_limit_are_not_partial_reports(self):
        other=get_user_model().objects.create_user('foreign-statement');foreign=BankAccount.objects.create(owner=other,name='Чужой')
        with self.assertRaises(Http404):account_statement(self.user,self.scope,foreign,date(2026,1,1),date(2026,1,1))
        CashTransaction.objects.create(account=self.account,category=self.category,occurred_on=date(2026,1,1),amount='.10',description='Доход')
        with patch('cashflow.statements.MAX_ROWS',0):
            with self.assertRaises(ValidationError):account_statement(self.user,self.scope,self.account,date(2026,1,1),date(2026,1,1))
        self.client.force_login(self.user)
        self.assertContains(self.client.get('/statement/',{'account':self.account.pk,'start':'2026-01-01','end':'2026-01-01'}),'Входящий остаток')
