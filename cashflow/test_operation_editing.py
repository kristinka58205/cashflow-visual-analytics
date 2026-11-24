from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import BankAccount, CashCategory, CashTransaction, LedgerEvent


class OperationEditingTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('editing')
        self.client.force_login(self.user)
        self.account=BankAccount.objects.create(owner=self.user,name='Main')
        self.category=CashCategory.objects.create(owner=self.user,name='Sale',kind='income')
        self.entry=CashTransaction.objects.create(account=self.account,category=self.category,description='Original',amount='10.00')
        self.url=reverse('transaction_edit',args=[self.entry.pk])
        self.delete_url=reverse('transaction_delete',args=[self.entry.pk])
        self.values={'account':self.account.pk,'category':self.category.pk,'amount':'20.10','occurred_on':timezone.localdate(),'description':'Changed','expected_revision':1}

    def test_second_tab_cannot_overwrite_or_delete_new_revision(self):
        first=self.client.get(self.url)
        self.assertEqual(first.context['form'].initial['expected_revision'],1)
        self.assertEqual(self.client.post(self.url,self.values).status_code,302)
        self.entry.refresh_from_db();self.assertEqual(self.entry.revision,2)
        stale=self.client.post(self.url,{**self.values,'amount':'99.99'})
        self.assertContains(stale,'уже изменена в другой вкладке')
        self.entry.refresh_from_db();self.assertEqual(str(self.entry.amount),'20.10')
        self.assertEqual(LedgerEvent.objects.filter(action='updated').count(),1)
        self.client.post(self.delete_url,{'expected_revision':1})
        self.assertTrue(CashTransaction.objects.filter(pk=self.entry.pk).exists())
        self.client.post(self.delete_url,{'expected_revision':2})
        self.assertFalse(CashTransaction.objects.filter(pk=self.entry.pk).exists())

    def test_missing_and_invalid_revision_cannot_mutate(self):
        for value in ['', 'bad',0,-1,999]:
            response=self.client.post(self.url,{**self.values,'expected_revision':value})
            self.assertEqual(response.status_code,200)
            self.client.post(self.delete_url,{'expected_revision':value})
            self.entry.refresh_from_db();self.assertEqual(self.entry.revision,1)
            self.assertEqual(self.entry.description,'Original')
        self.assertFalse(LedgerEvent.objects.exists())

    def test_invalid_financial_fields_do_not_consume_revision(self):
        self.client.post(self.url,{**self.values,'amount':'-1'})
        self.entry.refresh_from_db();self.assertEqual(self.entry.revision,1)
        self.assertEqual(self.client.post(self.url,self.values).status_code,302)

    def test_other_user_cannot_access_revision_or_mutate(self):
        self.client.force_login(get_user_model().objects.create_user('outside'))
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.assertEqual(self.client.post(self.url,self.values).status_code,404)
        self.assertEqual(self.client.post(self.delete_url,{'expected_revision':1}).status_code,404)
