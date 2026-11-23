import csv,io
from datetime import date
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from .access import LedgerScope
from .dimensions import dimension_report
from .models import BankAccount,CashCategory,CashProject,CashTransaction,CashTransfer,Counterparty
from .services import filtered_transactions


class DimensionTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('dimensions')
        self.scope=LedgerScope(self.user)
        self.account=BankAccount.objects.create(owner=self.user,name='Main')
        self.income=CashCategory.objects.create(owner=self.user,name='Sales',kind='income')
        self.expense=CashCategory.objects.create(owner=self.user,name='Costs',kind='expense')
        self.project=CashProject.objects.create(owner=self.user,name='=Project')
        self.party=Counterparty.objects.create(owner=self.user,name='Party')
        self.values={'start':date(2026,1,1),'end':date(2026,1,31),'dimension':'project','order':'net'}
        self.client.force_login(self.user)

    def entry(self,amount,category=None,**values):
        return CashTransaction.objects.create(account=self.account,category=category or self.income,
            occurred_on=date(2026,1,2),amount=amount,description='Recorded',**values)

    def test_groups_preserve_exact_totals_unassigned_and_drilldown(self):
        self.entry('.10',project=self.project,counterparty=self.party)
        self.entry('.20',project=self.project,counterparty=self.party)
        self.entry('.15',self.expense)
        second=BankAccount.objects.create(owner=self.user,name='Second')
        CashTransfer.objects.create(owner=self.user,from_account=self.account,to_account=second,amount='.05',
            occurred_on=date(2026,1,2),description='Internal',command_key=uuid4())
        report=dimension_report(self.user,self.scope,**self.values)
        self.assertEqual(report['totals'],{'income':Decimal('.30'),'expense':Decimal('.15'),'net':Decimal('.15')})
        self.assertEqual(report['count'],3)
        self.assertEqual([(row['name'],row['net']) for row in report['rows']],[('=Project',Decimal('.30')),('Без проекта',Decimal('-.15'))])
        null=next(row for row in report['rows'] if row['id'] is None)
        self.assertIn('unassigned=project',null['operations_query'])
        self.assertEqual(filtered_transactions(self.user,{'unassigned':'project'}).count(),1)
        report=dimension_report(self.user,self.scope,**{**self.values,'dimension':'counterparty'})
        self.assertEqual(report['totals']['net'],Decimal('.15'))

    def test_foreign_scope_rejection_limit_and_empty_summary(self):
        other=get_user_model().objects.create_user('foreign-dimension')
        foreign=BankAccount.objects.create(owner=other,name='Foreign')
        category=CashCategory.objects.create(owner=other,name='Secret',kind='income')
        CashTransaction.objects.create(account=foreign,category=category,amount='999',description='Secret',occurred_on=date(2026,1,2))
        self.assertEqual(dimension_report(self.user,self.scope,**self.values)['count'],0)
        with self.assertRaises(PermissionDenied):dimension_report(other,self.scope,**self.values)
        self.entry('.10')
        with patch('cashflow.dimensions.MAX_GROUPS',0),self.assertRaises(ValidationError):dimension_report(self.user,self.scope,**self.values)
        values={**self.values,'start':date(2026,2,1),'end':date(2026,2,1),'format':'csv'}
        response=self.client.get('/analytics/dimensions/',values)
        rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['net'],'0.00')

    def test_csv_formula_guard_and_invalid_parameters(self):
        self.entry('.10',project=self.project)
        response=self.client.get('/analytics/dimensions/',{**self.values,'format':'csv'})
        self.assertEqual(response['Cache-Control'],'private, no-store')
        rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(rows[1]['name'],"'=Project")
        self.assertEqual(rows[0]['income'],'0.10')
        self.assertContains(self.client.get('/analytics/dimensions/',self.values),'=Project')
        self.assertEqual(self.client.get('/analytics/dimensions/',{**self.values,'dimension':'owner','format':'csv'}).status_code,400)
