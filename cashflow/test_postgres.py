from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.test import SimpleTestCase, TransactionTestCase, skipUnlessDBFeature
from config.database import postgres_database
from .access import LedgerScope
from .models import BankAccount,CashCategory,PlannedPayment,CashTransaction,LedgerEvent
from .organizations import create_organization
from .payments import execute_payment


class DatabaseConfigurationTests(SimpleTestCase):
    def test_ssl_and_credentials_are_preserved_without_ignored_options(self):
        config=postgres_database('postgresql://user:p%40ss@localhost:5433/finance?sslmode=require&connect_timeout=5')
        self.assertEqual(config['PASSWORD'],'p@ss')
        self.assertEqual(config['OPTIONS'],{'sslmode':'require','connect_timeout':5})
        for value in ['sqlite:///x','postgresql://localhost/','postgresql://localhost/db?sslmode=wrong','postgresql://localhost/db?sslmode=disable&sslmode=require']:
            with self.assertRaises(ImproperlyConfigured):postgres_database(value)


class PaymentConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_execution_is_one_operation_and_one_event(self):
        user=get_user_model().objects.create_user('concurrent-finance')
        org=create_organization(user,name='Проверка конкуренции')
        account=BankAccount.objects.create(owner=user,organization=org,name='Счёт')
        category=CashCategory.objects.create(owner=user,organization=org,name='Оплата',kind='expense')
        payment=PlannedPayment.objects.create(account=account,category=category,due_on=date(2026,1,1),amount='12.34',description='Однократный платёж')
        barrier=Barrier(2)
        def run():
            try:
                local_user=get_user_model().objects.get(pk=user.pk)
                local_scope=LedgerScope(local_user,org)
                barrier.wait(timeout=10)
                result=execute_payment(local_user,payment.pk,date(2026,1,1),scope=local_scope, expected_revision=1)
                return result.pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:run(),range(2)))
        self.assertEqual(results,[CashTransaction.objects.get().pk]*2)
        self.assertEqual(CashTransaction.objects.count(),1)
        self.assertEqual(CashTransaction.objects.get().amount,Decimal('12.34'))
        self.assertEqual(LedgerEvent.objects.filter(action='paid').count(),1)
