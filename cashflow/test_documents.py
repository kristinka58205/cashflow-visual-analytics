from datetime import date
from io import BytesIO
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from pypdf import PdfReader
from .access import LedgerScope
from .documents import report_scope,render_cashflow_pdf
from .models import BankAccount,CashCategory,CashTransaction,OrganizationMembership
from .organizations import create_organization


class CashflowDocumentTests(TestCase):
    def test_pdf_uses_current_scope_and_exact_decimal_summary(self):
        owner=get_user_model().objects.create_user('doc-owner')
        viewer=get_user_model().objects.create_user('doc-viewer')
        org=create_organization(owner,name='Учебное предприятие')
        OrganizationMembership.objects.create(user=viewer,organization=org,role='viewer')
        account=BankAccount.objects.create(owner=owner,organization=org,name='Счёт',opening_balance='100.10')
        category=CashCategory.objects.create(owner=owner,organization=org,name='Выручка <b>буквально</b>',kind='income')
        CashTransaction.objects.create(account=account,category=category,amount='20.20',description='Оплата',occurred_on=date(2026,1,1))
        private=BankAccount.objects.create(owner=owner,name='Личный',opening_balance='999999.99')
        self.client.force_login(viewer);self.client.post(f'/workspace/{org.pk}/')
        response=self.client.get('/reports/cashflow.pdf',{'start':'2026-01-01','end':'2026-01-31'})
        self.assertEqual(response.status_code,200)
        text=''.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
        for expected in ['Учебное предприятие','120,30','100,10','20,20','<b>буквально</b>','Жукова']:
            self.assertIn(expected,text)
        self.assertNotIn('999999,99',text)
        self.assertContains(self.client.get('/analytics/',{'start':'2026-01-01','end':'2026-01-31'}),'/reports/cashflow.pdf?start=2026-01-01&amp;end=2026-01-31')
        self.assertEqual(self.client.get('/reports/cashflow.pdf',{'start':'bad'}).status_code,400)
        scope=LedgerScope(viewer,org)
        OrganizationMembership.objects.filter(user=viewer).delete()
        with self.assertRaises(PermissionDenied):
            with report_scope(viewer,scope):pass

    def test_long_report_has_repeated_headers_and_page_numbers(self):
        from decimal import Decimal
        report={'summary':dict.fromkeys(['opening','income','expense','net','closing'],Decimal('0.00')),'months':[],
            'categories':[{'kind':'expense','name':'Длинная статья '+str(i),'amount':Decimal('1.23'),'share':Decimal('1')} for i in range(100)]}
        pdf=render_cashflow_pdf('Учебный пример',date(2026,1,1),date(2026,1,31),report)
        pages=PdfReader(BytesIO(pdf)).pages
        self.assertGreater(len(pages),2)
        self.assertTrue(all('Страница' in page.extract_text() for page in pages))
