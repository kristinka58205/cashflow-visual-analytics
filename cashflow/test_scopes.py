from uuid import uuid4
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .access import LedgerScope, write_scope
from .budgets import budget_overview
from .imports import parse_import, apply_import
from .models import BankAccount, CashCategory, CashTransaction, Counterparty, ExpenseBudget, ImportBatch, OrganizationMembership, PlannedPayment
from .organizations import create_organization
from .payments import execute_payment
from .services import financial_summary


class SharedLedgerTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user("scope-owner")
        self.editor = users.objects.create_user("scope-editor")
        self.viewer = users.objects.create_user("scope-viewer")
        self.outsider = users.objects.create_user("scope-outside")
        self.organization = create_organization(self.owner, name="Наше предприятие")
        self.second = create_organization(self.outsider, name="Другое предприятие")
        for user, role in [(self.editor, "editor"), (self.viewer, "viewer")]:
            OrganizationMembership.objects.create(user=user, organization=self.organization, role=role)
        self.account = BankAccount.objects.create(owner=self.owner, organization=self.organization, name="Main")
        self.category = CashCategory.objects.create(owner=self.owner, organization=self.organization, name="Sales", kind="income")
        self.personal = BankAccount.objects.create(owner=self.owner, name="Main")
        self.personal_category = CashCategory.objects.create(owner=self.owner, name="Sales", kind="income")
        self.foreign_category = CashCategory.objects.create(owner=self.outsider, organization=self.second, name="Foreign", kind="income")
        self.today = timezone.localdate()

    def select(self, user):
        self.client.force_login(user)
        return self.client.post(reverse("workspace_select", args=[self.organization.pk]))

    def test_read_scope_separates_personal_other_organization_and_shared_rows(self):
        CashTransaction.objects.create(account=self.account, category=self.category, amount="10.10", description="CompanyEntry")
        CashTransaction.objects.create(account=self.personal, category=self.personal_category, amount="999", description="PrivateEntry")
        self.select(self.viewer)
        response = self.client.get(reverse("transaction_list"))
        self.assertContains(response, "CompanyEntry")
        self.assertNotContains(response, "PrivateEntry")
        self.assertNotContains(response, "Добавить операцию")
        result = financial_summary(self.viewer, scope=LedgerScope(self.viewer, self.organization))
        self.assertEqual(result["income"], Decimal("10.10"))
        response = self.client.get(reverse("transaction_export"))
        contents = b"".join(response.streaming_content).decode("utf-8-sig")
        self.assertIn("CompanyEntry", contents);self.assertNotIn("PrivateEntry", contents)

    def test_editor_uses_shared_references_and_creator_is_preserved(self):
        self.select(self.editor)
        self.client.post(reverse("catalog_create", args=["categories"]), {"name": "Shared costs", "kind": "expense"})
        category = CashCategory.objects.get(name="Shared costs")
        self.assertEqual((category.owner, category.organization), (self.editor, self.organization))
        self.client.post(reverse("transaction_create"), {"command_key": uuid4(), "account": self.account.pk, "category": category.pk,
            "amount": "12.34", "occurred_on": self.today, "description": "Оплата"})
        self.assertEqual(CashTransaction.objects.get().amount, Decimal("12.34"))
        self.client.post(reverse("catalog_edit", args=["accounts", self.account.pk]), {"name": "Main shared", "opening_balance": "20"})
        self.account.refresh_from_db();self.assertEqual(self.account.owner, self.owner)

    def test_readonly_role_cannot_mutate_any_finance_workflow(self):
        self.select(self.viewer)
        for name, args in [("catalog_create", ["accounts"]), ("transaction_create", []), ("budget_create", []),
                           ("payment_create", []), ("import_upload", [])]:
            self.assertEqual(self.client.post(reverse(name, args=args), {}).status_code, 403, name)
        with self.assertRaises(PermissionDenied):
            with write_scope(self.viewer, LedgerScope(self.viewer, self.organization)):
                self.fail("Read-only role entered write scope")

    def test_cross_scope_relations_are_rejected_even_with_same_creator(self):
        for category in [self.personal_category, self.foreign_category]:
            with self.assertRaises(ValidationError):
                CashTransaction(account=self.account, category=category, amount="10", description="Подмена").full_clean()
        party = Counterparty.objects.create(owner=self.editor, organization=self.organization, name="Partner")
        CashTransaction(account=self.account, category=self.category, counterparty=party, amount="10", description="Допустимо").full_clean()

    def test_removed_membership_fails_next_request_and_cached_write_scope(self):
        self.select(self.editor)
        stale = LedgerScope(self.editor, self.organization)
        OrganizationMembership.objects.filter(user=self.editor, organization=self.organization).delete()
        self.assertEqual(self.client.get(reverse("transaction_list")).status_code, 403)
        with self.assertRaises(PermissionDenied):
            with write_scope(self.editor, stale):
                pass
        self.client.post(reverse("workspace_select", args=[0]))
        self.assertEqual(self.client.get(reverse("transaction_list")).status_code, 200)

    def test_payment_and_import_are_shared_and_idempotent(self):
        scope = LedgerScope(self.editor, self.organization)
        payment = PlannedPayment.objects.create(account=self.account, category=self.category, due_on=self.today, amount="5.10", description="Planned")
        first = execute_payment(self.editor, payment.pk, self.today, scope=scope)
        self.assertEqual(first.pk, execute_payment(self.owner, payment.pk, self.today, scope=LedgerScope(self.owner, self.organization)).pk)
        payload = ("date;account;category;kind;amount;description;counterparty;project\n"+f"{self.today};Main;Sales;income;10.10;Imported;;\n").encode()
        rows = parse_import(self.editor, payload, scope=scope)
        batch = ImportBatch.objects.create(owner=self.owner, organization=self.organization, filename="shared.csv", sha256="a"*64, rows=rows)
        apply_import(self.editor, batch.pk, scope=scope)
        apply_import(self.owner, batch.pk, scope=LedgerScope(self.owner, self.organization))
        self.assertEqual(CashTransaction.objects.count(), 2)

    def test_budgets_aggregate_all_creators_and_stay_unique(self):
        category = CashCategory.objects.create(owner=self.editor, organization=self.organization, name="Rent", kind="expense")
        month = self.today.replace(day=1)
        ExpenseBudget.objects.create(owner=self.owner, category=category, month_start=month, limit="20")
        CashTransaction.objects.create(account=self.account, category=category, amount="20.01", description="Rent")
        scope = LedgerScope(self.editor, self.organization)
        self.assertEqual(budget_overview(self.editor, month, scope=scope)[0]["remaining"], Decimal("-0.01"))
        self.select(self.editor)
        self.client.post(reverse("budget_create"), {"category": category.pk, "month_start": month.strftime("%Y-%m"), "limit": "30"})
        self.assertEqual(ExpenseBudget.objects.count(), 1)

    def test_switch_requires_membership_and_does_not_move_existing_records(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(reverse("workspace_select", args=[self.organization.pk])).status_code, 403)
        self.personal.refresh_from_db();self.assertIsNone(self.personal.organization_id)
        self.select(self.owner)
        self.assertNotContains(self.client.get(reverse("catalog")), "Начальный остаток: 999")
        self.client.post(reverse("workspace_select", args=[0]))
        self.assertContains(self.client.get(reverse("catalog")), "Личный учёт")
