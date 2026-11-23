import csv,io
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from .access import LedgerScope
from .quality import duplicate_report
from .models import BankAccount,CashCategory,CashTransaction,CashProject


class DuplicateTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('quality')
        self.account=BankAccount.objects.create(owner=self.user,name='Main')
        self.category=CashCategory.objects.create(owner=self.user,name='Sales',kind='income')
        self.scope=LedgerScope(self.user)
        self.values={'account':self.account,'category':self.category,'occurred_on':date(2026,1,2),'amount':'.10','description':'=same'}
        self.period={'start':date(2026,1,1),'end':date(2026,1,31)}

    def test_exact_groups_do_not_merge_different_analytics_or_change_data(self):
        for _ in range(3):CashTransaction.objects.create(**self.values)
        project=CashProject.objects.create(owner=self.user,name='Other')
        CashTransaction.objects.create(**self.values,project=project)
        CashTransaction.objects.create(**{**self.values,'description':' =same'})
        report=duplicate_report(self.user,self.scope,**self.period)
        self.assertEqual((report['checked'],report['matched'],len(report['groups'])),(5,3,1))
        self.assertEqual(report['possible_extra']['income'],Decimal('.20'))
        self.assertEqual(CashTransaction.objects.count(),5)
        self.client.force_login(self.user)
        response=self.client.get('/quality/',{**self.period,'format':'csv'})
        rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(rows[0]['description'],"'=same")
        self.assertEqual(len(rows[0]['operation_ids'].split(',')),3)

    def test_scope_limit_and_foreign_account_are_rejected(self):
        other=get_user_model().objects.create_user('quality-other')
        foreign=BankAccount.objects.create(owner=other,name='Foreign')
        with self.assertRaises(ValidationError):duplicate_report(self.user,self.scope,account=foreign,**self.period)
        with self.assertRaises(PermissionDenied):duplicate_report(other,self.scope,**self.period)
        CashTransaction.objects.create(**self.values)
        with patch('cashflow.quality.MAX_OPERATIONS',0),self.assertRaises(ValidationError):duplicate_report(self.user,self.scope,**self.period)
        self.assertEqual(duplicate_report(self.user,self.scope,**self.period)['matched'],0)

    def test_preview_is_bounded_but_csv_has_all_identifiers(self):
        CashTransaction.objects.bulk_create([CashTransaction(**self.values) for _ in range(21)])
        report=duplicate_report(self.user,self.scope,**self.period)
        self.assertEqual(len(report['groups'][0]['preview_ids']),20)
        self.assertEqual(len(report['groups'][0]['ids']),21)
        self.client.force_login(self.user)
        self.assertContains(self.client.get('/quality/',self.period),'совпадений: 21')
