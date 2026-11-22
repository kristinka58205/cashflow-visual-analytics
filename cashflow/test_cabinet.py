from django.contrib.auth import get_user_model
from django.test import TestCase
from .cabinet import PAGES
from .models import BankAccount, CashCategory, CashTransaction, OrganizationMembership
from .organizations import create_organization


class CabinetTests(TestCase):
    def setUp(self):
        self.staff=get_user_model().objects.create_user('staff',is_staff=True)
        self.user=get_user_model().objects.create_user('ordinary')
        self.org=create_organization(self.user,name='Team')

    def test_all_screens_require_staff_and_do_not_expose_finances(self):
        CashTransaction.objects.create(account=BankAccount.objects.create(owner=self.user,organization=self.org,name='PrivateBankName'),category=CashCategory.objects.create(owner=self.user,organization=self.org,name='PrivateCategory',kind='income'),amount='999.12',description='SecretFinancialDescription')
        for section,_ in PAGES:
            self.client.force_login(self.user)
            self.assertEqual(self.client.get(f'/cabinet/{section}/').status_code,403)
            self.client.force_login(self.staff)
            response=self.client.get(f'/cabinet/{section}/')
            self.assertEqual(response.status_code,200)
            self.assertNotContains(response,'SecretFinancialDescription')
            self.assertNotContains(response,'PrivateBankName')
        self.assertEqual(self.client.get('/cabinet/unknown/').status_code,404)

    def test_moderation_protects_staff_and_integrity_is_diagnostic(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post('/cabinet/accounts/action/',{'user_id':self.staff.pk,'action':'deactivate'}).status_code,404)
        self.assertEqual(self.client.post('/cabinet/accounts/action/',{'user_id':self.user.pk,'action':'deactivate'}).status_code,302)
        self.user.refresh_from_db();self.assertFalse(self.user.is_active)
        self.client.post('/cabinet/accounts/action/',{'user_id':self.user.pk,'action':'activate'})
        self.user.refresh_from_db();self.assertTrue(self.user.is_active)
        OrganizationMembership.objects.filter(organization=self.org,user=self.user).delete()
        self.assertContains(self.client.get('/cabinet/integrity/'),'Отсутствует административное членство')
        self.assertFalse(OrganizationMembership.objects.filter(organization=self.org,user=self.user).exists())
