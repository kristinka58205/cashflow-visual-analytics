from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .access import LedgerScope
from .models import BankAccount, CashCategory, CashTransaction, LedgerEvent, OrganizationMembership, PlannedPayment
from .organizations import create_organization
from .payments import execute_payment


class AuditTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("audit-owner")
        self.viewer = get_user_model().objects.create_user("audit-viewer")
        self.organization = create_organization(self.owner, name="Audit Org")
        OrganizationMembership.objects.create(organization=self.organization, user=self.viewer, role="viewer")
        self.account = BankAccount.objects.create(owner=self.owner, organization=self.organization, name="Main")
        self.category = CashCategory.objects.create(owner=self.owner, organization=self.organization, name="Sales", kind="income")
        self.client.force_login(self.owner)
        self.client.post(reverse("workspace_select", args=[self.organization.pk]))

    def test_mutation_records_actor_and_readers_cannot_see_personal_events(self):
        self.client.post(reverse("transaction_create"), {"command_key": uuid4(), "account": self.account.pk, "category": self.category.pk,
            "amount": "10.10", "description": "Company change", "occurred_on": timezone.localdate()})
        event = LedgerEvent.objects.get()
        self.assertEqual((event.actor, event.organization, event.action), (self.owner, self.organization, "created"))
        LedgerEvent.objects.create(actor=self.owner, actor_name=self.owner.username, action="created", entity="catalog", object_id=99, summary="Private audit")
        self.client.force_login(self.viewer)
        self.client.post(reverse("workspace_select", args=[self.organization.pk]))
        response = self.client.get(reverse("audit_list"))
        self.assertContains(response, "Company change")
        self.assertNotContains(response, "Private audit")
        response = self.client.get(reverse("audit_export"), {"entity": "operation"})
        self.assertIn("Company change", response.content.decode("utf-8-sig"))
        self.assertNotIn("Private audit", response.content.decode("utf-8-sig"))

    def test_event_failure_rolls_back_financial_mutation(self):
        with patch("cashflow.audit.LedgerEvent.objects.create", side_effect=RuntimeError("Audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("transaction_create"), {"command_key": uuid4(), "account": self.account.pk, "category": self.category.pk,
                    "amount": "10", "description": "Must roll back", "occurred_on": timezone.localdate()})
        self.assertFalse(CashTransaction.objects.exists())

    def test_repeated_payment_does_not_duplicate_events(self):
        payment = PlannedPayment.objects.create(account=self.account, category=self.category, amount="10", due_on=timezone.localdate(), description="Payment")
        scope = LedgerScope(self.owner, self.organization)
        execute_payment(self.owner, payment.pk, timezone.localdate(), scope=scope, expected_revision=1)
        execute_payment(self.owner, payment.pk, timezone.localdate(), scope=scope, expected_revision=1)
        self.assertEqual(scope.events().filter(action="paid").count(), 1)
        self.assertEqual(scope.events().count(), 2)
