from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import BankAccount, CashCategory, CashProject, CashTransaction, Counterparty


class BusinessCatalogTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("finance")
        self.other = get_user_model().objects.create_user("outsider")
        self.client.force_login(self.user)
        self.account = BankAccount.objects.create(owner=self.user, name="Счёт")
        self.category = CashCategory.objects.create(owner=self.user, name="Продажи", kind="income")

    def test_catalog_crud_is_private_and_preserves_linked_operations(self):
        for kind, model in [("counterparties", Counterparty), ("projects", CashProject)]:
            url = reverse("catalog_create", args=[kind])
            self.assertRedirects(self.client.post(url, {"name": kind, "description": "Тест", "owner": self.other.pk}), reverse("catalog"))
            item = model.objects.get()
            self.assertEqual(item.owner, self.user)
            foreign = model.objects.create(owner=self.other, name="Закрытые сведения")
            self.assertNotContains(self.client.get(reverse("catalog")), foreign.name)
            self.assertEqual(self.client.post(reverse("catalog_edit", args=[kind, foreign.pk]), {}).status_code, 404)
            field = "counterparty" if model is Counterparty else "project"
            CashTransaction.objects.create(account=self.account, category=self.category,
                amount="15.00", description="История", **{field: item})
            self.client.post(reverse("catalog_delete", args=[kind, item.pk]))
            self.assertTrue(model.objects.filter(pk=item.pk).exists())

    def test_tax_id_and_duplicate_validation(self):
        url = reverse("catalog_create", args=["counterparties"])
        self.assertContains(self.client.post(url, {"name": "Клиент", "tax_id": "12ABC"}), "10 или 12 цифр")
        self.client.post(url, {"name": "Клиент", "tax_id": "1234567890"})
        self.assertContains(self.client.post(url, {"name": "Клиент"}), "уже используется")
        self.assertEqual(Counterparty.objects.count(), 1)

    def test_foreign_links_rejected_in_form_and_model(self):
        for field, model in [("counterparty", Counterparty), ("project", CashProject)]:
            foreign = model.objects.create(owner=self.other, name=field)
            payload = {"account": self.account.pk, "category": self.category.pk,
                "amount": "10", "description": "Подмена", "occurred_on": timezone.localdate(), field: foreign.pk}
            self.client.post(reverse("transaction_create"), payload)
            self.assertFalse(CashTransaction.objects.exists())
            entry = CashTransaction(account=self.account, category=self.category, amount="10", description="Подмена", **{field: foreign})
            with self.assertRaises(ValidationError):
                entry.full_clean()

    def test_existing_operations_allow_empty_optional_links(self):
        entry = CashTransaction(account=self.account, category=self.category, amount="10", description="Без аналитик")
        entry.full_clean()
        entry.save()
        self.assertContains(self.client.get(reverse("transaction_list")), "Без аналитик")
