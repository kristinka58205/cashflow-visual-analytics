from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from .access import LedgerScope
from .models import BankAccount, CashCategory, CashTransaction
from .money import CashSum
from .reconciliation import balance_at
from .services import financial_summary, filtered_transactions, transaction_totals


class ExactMoneyTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('exact-money')
        self.scope = LedgerScope(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name='Счёт', opening_balance='9999999999999999.99')
        self.income = CashCategory.objects.create(owner=self.user, name='Доход', kind='income')
        self.expense = CashCategory.objects.create(owner=self.user, name='Расход', kind='expense')

    def entry(self, amount, category=None):
        return CashTransaction.objects.create(account=self.account, category=category or self.income,
            amount=Decimal(amount), description='Точные копейки', occurred_on=date(2026, 1, 1))

    def test_full_18_digit_roundtrip_and_aggregate_exceeding_one_field(self):
        self.account.refresh_from_db()
        self.assertEqual(self.account.opening_balance, Decimal('9999999999999999.99'))
        item = self.entry('9999999999999999.99')
        item.refresh_from_db()
        self.assertEqual(item.amount, Decimal('9999999999999999.99'))
        self.entry('0.01')
        self.entry('0.02', self.expense)
        totals = transaction_totals(self.scope.entries())
        self.assertEqual(totals['income'], Decimal('10000000000000000.00'))
        self.assertEqual(totals['net'], Decimal('9999999999999999.98'))
        summary = financial_summary(self.user, scope=self.scope)
        self.assertEqual(summary['closing'], Decimal('19999999999999999.97'))
        self.assertEqual(balance_at(self.scope, self.account, date(2026, 1, 1)), summary['closing'])

    def test_numeric_ranges_order_and_distinct_forms_of_same_value(self):
        for amount in ['100.00', '9.00', '10.00', '0.09']:
            self.entry(amount)
        values = list(filtered_transactions(self.user, {'minimum': Decimal('9'), 'maximum': Decimal('100'), 'order': 'smallest'}).values_list('amount', flat=True))
        self.assertEqual(values, [Decimal('9'), Decimal('10'), Decimal('100')])
        self.assertEqual(self.scope.entries().get(amount=Decimal('9.000')).amount, Decimal('9'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.entry('-0.01')
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.entry('0')

    def test_small_sums_are_exact_and_empty_sum_is_none(self):
        self.assertIsNone(self.scope.entries().aggregate(total=CashSum('amount'))['total'])
        for amount in ['0.10', '0.20', '0.01']:
            self.entry(amount)
        self.assertEqual(transaction_totals(self.scope.entries())['income'], Decimal('0.31'))
