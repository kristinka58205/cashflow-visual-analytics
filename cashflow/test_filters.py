from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import BankAccount, CashCategory, CashTransaction, Counterparty


class TransactionFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("filter-user")
        self.client.force_login(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name="Основной")
        self.income = CashCategory.objects.create(owner=self.user, name="Продажи", kind="income")
        self.expense = CashCategory.objects.create(owner=self.user, name="Аренда", kind="expense")
        self.party = Counterparty.objects.create(owner=self.user, name="Customer Alpha")
        self.today = timezone.localdate()
        for days, amount, category in [(0, "10.10", self.income), (0, "3.20", self.expense), (3, "90.00", self.income)]:
            CashTransaction.objects.create(account=self.account, category=category, counterparty=self.party,
                occurred_on=self.today-timedelta(days=days), amount=amount, description="Платёж")

    def test_combined_filters_and_exact_totals(self):
        response = self.client.get(reverse("transaction_list"), {"search": "alpha", "start": self.today,
            "end": self.today, "account": self.account.pk, "counterparty": self.party.pk})
        self.assertEqual(response.context["page"].paginator.count, 2)
        self.assertEqual(response.context["totals"]["net"], Decimal("6.90"))
        response = self.client.get(reverse("transaction_list"), {"minimum": "10", "maximum": "20", "kind": "income"})
        self.assertEqual(response.context["page"].paginator.count, 1)

    def test_invalid_ranges_and_foreign_choices_do_not_show_unfiltered_data(self):
        other = get_user_model().objects.create_user("other-filter")
        foreign = BankAccount.objects.create(owner=other, name="Закрытый счёт")
        for values in [{"start": self.today, "end": self.today-timedelta(days=1)},
                       {"minimum": "30", "maximum": "10"}, {"account": foreign.pk}, {"start": "invalid"}]:
            response = self.client.get(reverse("transaction_list"), values)
            self.assertTrue(response.context["filters"].errors)
            self.assertEqual(response.context["page"].paginator.count, 0)

    def test_ordering_and_pagination_keep_filters(self):
        CashTransaction.objects.bulk_create([CashTransaction(account=self.account, category=self.income,
            occurred_on=self.today, amount="12.00", description="Pagination") for _ in range(27)])
        response = self.client.get(reverse("transaction_list"), {"search": "Pagination", "order": "largest"})
        self.assertEqual(len(response.context["page"]), 25)
        self.assertContains(response, "search=Pagination&amp;order=largest&amp;page=2")
        second = self.client.get(reverse("transaction_list"), {"search": "Pagination", "page": 2})
        self.assertEqual(len(second.context["page"]), 2)
        largest = self.client.get(reverse("transaction_list"), {"order": "largest"})
        self.assertEqual(largest.context["page"][0].amount, Decimal("90.00"))
