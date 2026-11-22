from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from .models import BankAccount, CashCategory, CashTransaction, OrganizationMembership, TransactionReceipt
from .organizations import create_organization


class ReceiptTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('owner')
        self.viewer = get_user_model().objects.create_user('viewer')
        self.org = create_organization(self.owner, name='Общее')
        OrganizationMembership.objects.create(organization=self.org,user=self.viewer,role='viewer')
        self.entry = CashTransaction.objects.create(account=BankAccount.objects.create(owner=self.owner,organization=self.org,name='Счёт'),category=CashCategory.objects.create(owner=self.owner,organization=self.org,name='Доход',kind='income'),amount='10',description='Оплата')
        self.client.force_login(self.owner)
        self.client.post(f'/workspace/{self.org.pk}/')

    def upload(self, name='bill.txt', data=b'bill'):
        return self.client.post(f'/operations/{self.entry.pk}/receipts/upload/', {'file':SimpleUploadedFile(name,data)})

    def test_shared_readonly_download_revocation_and_no_other_scope(self):
        self.assertEqual(self.upload().status_code,302)
        item=TransactionReceipt.objects.get()
        self.client.force_login(self.viewer)
        self.client.post(f'/workspace/{self.org.pk}/')
        response=self.client.get(f'/receipts/{item.pk}/download/')
        self.assertEqual(b''.join(response.streaming_content),b'bill')
        self.assertIn('attachment',response['Content-Disposition'])
        self.assertEqual(self.upload().status_code,403)
        self.assertEqual(self.client.post(f'/receipts/{item.pk}/delete/').status_code,403)
        OrganizationMembership.objects.filter(user=self.viewer).delete()
        self.assertEqual(self.client.get(f'/receipts/{item.pk}/download/').status_code,403)
        self.client.post('/workspace/0/')
        self.assertEqual(self.client.get(f'/receipts/{item.pk}/download/').status_code,404)

    def test_validation_duplicate_quota_and_delete(self):
        self.assertEqual(self.upload('bad.pdf',b'not pdf').status_code,400)
        self.assertEqual(self.upload('bad.txt',b'\xff').status_code,400)
        self.assertEqual(self.upload().status_code,302)
        self.assertEqual(self.upload().status_code,400)
        with patch('cashflow.receipts.MAX_TOTAL',5):
            self.assertEqual(self.upload('new.txt',b'another').status_code,400)
        item=TransactionReceipt.objects.get()
        self.assertEqual(self.client.get(f'/receipts/{item.pk}/delete/').status_code,405)
        self.client.post(f'/receipts/{item.pk}/delete/')
        self.assertFalse(TransactionReceipt.objects.exists())
