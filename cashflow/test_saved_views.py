from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from .models import BankAccount, SavedTransactionView, OrganizationMembership
from .organizations import create_organization


class SavedViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('saved-owner')
        self.other = get_user_model().objects.create_user('saved-other')
        self.client.force_login(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name='Main')

    def save(self, **changes):
        return self.client.post(reverse('saved_view_save'), {'name':'Основные', 'query':f'account={self.account.pk}&minimum=0.10&start=2026-01-01&page=3', **changes})

    def test_roundtrip_canonical_values_ignores_pagination(self):
        self.assertEqual(self.save().status_code, 302)
        item=SavedTransactionView.objects.get()
        self.assertEqual(item.filters, {'account':str(self.account.pk),'minimum':'0.10','start':'2026-01-01'})
        response=self.client.get(reverse('saved_view_open',args=[item.pk]))
        self.assertEqual(response.status_code,302)
        self.assertIn('minimum=0.10',response.url)
        self.assertNotIn('page=',response.url)
        self.assertEqual(self.client.get(response.url).status_code,200)

    def test_unknown_ambiguous_and_foreign_filters_rejected(self):
        foreign=BankAccount.objects.create(owner=self.other,name='Foreign')
        for query in ['search=x&search=y', 'unknown=1',f'account={foreign.pk}', 'minimum=4&maximum=2']:
            self.assertEqual(self.save(query=query).status_code,400,query)
        self.assertFalse(SavedTransactionView.objects.exists())

    def test_missing_reference_does_not_broaden_query(self):
        self.save();item=SavedTransactionView.objects.get();self.account.delete()
        response=self.client.get(reverse('saved_view_open',args=[item.pk]))
        self.assertEqual(response.status_code,409)
        self.assertContains(response,'Данные не показаны',status_code=409)
        item.filters={'account':7};item.save()
        self.assertEqual(self.client.get(reverse('saved_view_open',args=[item.pk])).status_code,409)

    def test_preferences_are_private_and_delete_is_post_only_with_csrf(self):
        self.save();item=SavedTransactionView.objects.get()
        self.assertEqual(self.client.get(reverse('saved_view_delete',args=[item.pk])).status_code,405)
        strict=Client(enforce_csrf_checks=True);strict.force_login(self.user)
        self.assertEqual(strict.post(reverse('saved_view_delete',args=[item.pk])).status_code,403)
        self.client.force_login(self.other)
        for route in ['saved_view_open','saved_view_delete']:
            response=(self.client.post if route.endswith('delete') else self.client.get)(reverse(route,args=[item.pk]))
            self.assertEqual(response.status_code,404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('saved_view_delete',args=[item.pk])).status_code,302)
        self.assertFalse(SavedTransactionView.objects.exists())

    def test_duplicate_name_and_capacity_do_not_overwrite(self):
        self.save();self.assertEqual(self.save(query='search=changed').status_code,400)
        SavedTransactionView.objects.bulk_create([SavedTransactionView(owner=self.user,name=f'v-{n}',filters={}) for n in range(49)])
        self.assertEqual(self.save(name='Over limit').status_code,400)
        self.assertEqual(SavedTransactionView.objects.count(),50)

    def test_viewer_can_keep_own_preferences_only_in_selected_scope(self):
        org=create_organization(self.other,name='Team')
        OrganizationMembership.objects.create(organization=org,user=self.user,role='viewer')
        self.save()
        personal=SavedTransactionView.objects.get()
        self.client.post(reverse('workspace_select',args=[org.pk]))
        self.assertEqual(self.save(query='search=team').status_code,302)
        team=SavedTransactionView.objects.get(organization=org)
        self.assertEqual(self.client.get(reverse('saved_view_open',args=[personal.pk])).status_code,404)
        self.client.force_login(self.other)
        self.client.post(reverse('workspace_select',args=[org.pk]))
        self.assertNotContains(self.client.get(reverse('saved_view_list')),'Основные')
        self.client.force_login(self.user)
        self.client.post(reverse('workspace_select',args=[org.pk]))
        OrganizationMembership.objects.filter(organization=org,user=self.user).delete()
        self.assertEqual(self.client.post(reverse('saved_view_delete',args=[team.pk])).status_code,403)
        self.assertTrue(SavedTransactionView.objects.filter(pk=team.pk).exists())
