from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import TestCase
from django.utils import timezone
from .access import LedgerScope
from .models import BankAccount, CashCategory, CashTransaction, AccountReconciliation, LedgerEvent, OrganizationMembership
from .organizations import create_organization
from .reconciliation import reconcile


class ReconciliationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("finance")
        self.scope = LedgerScope(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name="Основной", opening_balance="0.10")
        self.category = CashCategory.objects.create(owner=self.user, name="Доход", kind="income")
        self.today = timezone.localdate()

    def test_exact_snapshot_repeat_and_later_staleness(self):
        entry = CashTransaction.objects.create(account=self.account, category=self.category,
            occurred_on=self.today-timedelta(days=1), amount="0.20", description="Приход")
        CashTransaction.objects.create(account=self.account, category=self.category, occurred_on=self.today,
            amount="100.00", description="После даты сверки")
        snapshot, created = reconcile(self.user, self.scope, self.account.pk, entry.occurred_on, Decimal("0.30"))
        self.assertTrue(created)
        self.assertEqual(snapshot.ledger_balance, Decimal("0.30"))
        self.assertFalse(reconcile(self.user, self.scope, self.account.pk, entry.occurred_on, Decimal("0.30"))[1])
        self.assertEqual(LedgerEvent.objects.filter(entity="reconciliation").count(), 1)
        entry.amount = "0.25"; entry.save()
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/reconciliation/"), "Учёт изменён")
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.ledger_balance, Decimal("0.30"))
        self.assertTrue(reconcile(self.user, self.scope, self.account.pk, entry.occurred_on, Decimal("0.30"))[1])

    def test_scope_role_future_and_revocation(self):
        viewer = get_user_model().objects.create_user("viewer")
        org = create_organization(self.user, name="Команда")
        OrganizationMembership.objects.create(user=viewer, organization=org, role="viewer")
        scope = LedgerScope(viewer, org)
        with self.assertRaises(PermissionDenied): reconcile(viewer, scope, self.account.pk, self.today, Decimal(0))
        with self.assertRaises(Http404): reconcile(viewer, LedgerScope(viewer), self.account.pk, self.today, Decimal(0))
        with self.assertRaises(ValidationError): reconcile(self.user, self.scope, self.account.pk, self.today+timedelta(days=1), Decimal(0))
        self.client.force_login(viewer)
        self.assertNotContains(self.client.get("/reconciliation/"), "Основной")
        OrganizationMembership.objects.filter(user=viewer).delete()
        with self.assertRaises(PermissionDenied): reconcile(viewer, scope, self.account.pk, self.today, Decimal(0))
        self.assertFalse(AccountReconciliation.objects.exists())

    def test_filtered_csv_keeps_original_snapshot_and_safe_labels(self):
        import csv,io
        self.account.name='=SUM(1)';self.account.save()
        day=self.today-timedelta(days=1)
        first,_=reconcile(self.user,self.scope,self.account.pk,day,Decimal('0.20'))
        reconcile(self.user,self.scope,self.account.pk,self.today,Decimal('0.30'))
        self.account.opening_balance='999.99';self.account.save()
        self.client.force_login(self.user)
        response=self.client.get('/reconciliation/',{'format':'csv','account':self.account.pk,'start':day,'end':day})
        self.assertEqual(response.status_code,200)
        rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['ledger_balance_at_check'],rows[0]['difference_at_check']),('0.10','0.10'))
        self.assertEqual(rows[0]['account'],"'=SUM(1)")
        self.assertEqual(response['Cache-Control'],'private, no-store')
        first.refresh_from_db();self.assertEqual(first.ledger_balance,Decimal('0.10'))

    def test_invalid_foreign_filters_and_export_limit_fail_closed(self):
        from unittest.mock import patch
        reconcile(self.user,self.scope,self.account.pk,self.today,Decimal('0.10'))
        self.client.force_login(self.user)
        for values in [{'start':self.today,'end':self.today-timedelta(days=1)},{'account':999999}]:
            self.assertEqual(self.client.get('/reconciliation/',{**values,'format':'csv'}).status_code,400)
        with patch('cashflow.reconciliation.MAX_EXPORT',0):
            response=self.client.get('/reconciliation/?format=csv')
            self.assertEqual(response.status_code,400);self.assertNotIn('Content-Disposition',response)
        other=get_user_model().objects.create_user('export-outsider');self.client.force_login(other)
        response=self.client.get('/reconciliation/?format=csv')
        self.assertEqual(len(response.content.decode('utf-8-sig').splitlines()),1)
