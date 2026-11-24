from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import BankAccount, CashCategory, CashTransaction, PlannedPayment
from .payments import execute_payment, save_payment


class PaymentTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("payment-user")
        self.client.force_login(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name="Счёт")
        self.category = CashCategory.objects.create(owner=self.user, name="Расход", kind="expense")
        self.today = timezone.localdate()
        self.payment = PlannedPayment.objects.create(account=self.account, category=self.category, due_on=self.today+timedelta(days=3), amount="100.10", description="Аренда")

    def test_execution_is_atomic_and_idempotent(self):
        first = execute_payment(self.user, self.payment.pk, self.today, expected_revision=1)
        second = execute_payment(self.user, self.payment.pk, self.today, expected_revision=1)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CashTransaction.objects.count(), 1)
        self.client.post(reverse("transaction_delete", args=[first.pk]))
        self.assertTrue(CashTransaction.objects.filter(pk=first.pk).exists())
        with self.assertRaises(ValidationError):
            save_payment(self.user, self.payment)
        self.assertContains(self.client.get(reverse("payment_list"), {"state": "paid"}), "Аренда")

    def test_future_actual_and_cancelled_payments_are_rejected(self):
        with self.assertRaises(ValidationError):
            execute_payment(self.user, self.payment.pk, self.today+timedelta(days=1), expected_revision=1)
        self.payment.refresh_from_db();self.assertEqual(self.payment.status, "planned")
        self.client.post(reverse("payment_cancel", args=[self.payment.pk]),{"expected_revision":1})
        with self.assertRaises(ValidationError):
            execute_payment(self.user, self.payment.pk, self.today, expected_revision=1)
        self.assertFalse(CashTransaction.objects.exists())

    def test_foreign_access_and_plan_form(self):
        self.assertContains(self.client.get(reverse("payment_create")), "Плановый платёж")
        self.client.post(reverse("payment_create"), {"account": self.account.pk, "category": self.category.pk,
            "due_on": self.today, "amount": "2.10", "description": "Новый"})
        self.assertEqual(PlannedPayment.objects.count(), 2)
        other = get_user_model().objects.create_user("payment-other")
        self.client.force_login(other)
        for action in ["payment_edit", "payment_execute", "payment_cancel"]:
            self.assertEqual(self.client.post(reverse(action, args=[self.payment.pk]), {}).status_code, 404)

    def test_old_editor_execution_and_cancel_cannot_use_changed_plan(self):
        payload={'account':self.account.pk,'category':self.category.pk,'due_on':self.payment.due_on,
            'amount':'200.20','description':'New plan','expected_revision':1}
        edit=reverse('payment_edit',args=[self.payment.pk])
        self.assertEqual(self.client.post(edit,payload).status_code,302)
        stale=self.client.post(edit,{**payload,'amount':'999.99'})
        self.assertContains(stale,'уже изменилась')
        self.client.post(reverse('payment_cancel',args=[self.payment.pk]),{'expected_revision':1})
        with self.assertRaises(ValidationError):execute_payment(self.user,self.payment.pk,self.today,expected_revision=1)
        self.payment.refresh_from_db()
        self.assertEqual((self.payment.status,self.payment.revision,str(self.payment.amount)),('planned',2,'200.20'))
        entry=execute_payment(self.user,self.payment.pk,self.today,expected_revision=2)
        self.assertEqual(str(entry.amount),'200.20')
        self.assertEqual(execute_payment(self.user,self.payment.pk,self.today,expected_revision=1).pk,entry.pk)
        self.assertEqual(CashTransaction.objects.count(),1)

    def test_service_rejects_status_injection_and_stale_service_object(self):
        self.payment.status='cancelled'
        with self.assertRaises(ValidationError):save_payment(self.user,self.payment,expected_revision=1)
        self.payment.refresh_from_db();self.assertEqual(self.payment.status,'planned')
        self.payment.amount='200.20';save_payment(self.user,self.payment,expected_revision=1)
        self.payment.amount='999'
        with self.assertRaises(ValidationError):save_payment(self.user,self.payment,expected_revision=1)
        self.payment.refresh_from_db();self.assertEqual(str(self.payment.amount),'200.20')

    def test_missing_version_never_cancels_or_executes_plan(self):
        self.client.post(reverse('payment_cancel',args=[self.payment.pk]),{})
        response=self.client.post(reverse('payment_execute',args=[self.payment.pk]),{'occurred_on':self.today})
        self.assertEqual(response.status_code,200)
        self.payment.refresh_from_db();self.assertEqual(self.payment.status,'planned')
        self.assertFalse(CashTransaction.objects.exists())
