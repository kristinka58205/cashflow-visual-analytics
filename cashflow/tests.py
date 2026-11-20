from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import BankAccount, CashCategory


class RegistrationTests(TestCase):
    def test_register_and_log_in(self):
        response = self.client.post(reverse("register"), {
            "username": "analyst", "password1": "Finance-Review-2048!", "password2": "Finance-Review-2048!",
        })
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), get_user_model().objects.get(username="analyst").pk)

    def test_public_home_has_author(self):
        self.assertContains(self.client.get(reverse("home")), "Жукова Кристина Дмитриевна")


class CatalogTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("analyst")
        self.other = get_user_model().objects.create_user("other")
        self.client.force_login(self.user)

    def test_account_owner_cannot_be_spoofed(self):
        self.client.post(reverse("catalog_create", args=["accounts"]), {"name": "Основной", "opening_balance": "123.45", "owner": self.other.pk})
        self.assertEqual(BankAccount.objects.get().owner, self.user)

    def test_duplicate_names_rejected(self):
        url = reverse("catalog_create", args=["accounts"])
        self.client.post(url, {"name": "Основной", "opening_balance": "0"})
        self.assertContains(self.client.post(url, {"name": "Основной", "opening_balance": "0"}), "уже используется")
        self.assertEqual(BankAccount.objects.count(), 1)

    def test_foreign_data_cannot_be_changed(self):
        account = BankAccount.objects.create(owner=self.other, name="Чужой")
        self.assertNotContains(self.client.get(reverse("catalog")), "Чужой")
        for action in ["catalog_edit", "catalog_delete"]:
            self.assertEqual(self.client.post(reverse(action, args=["accounts", account.pk]), {}).status_code, 404)

    def test_deletion_requires_confirmation(self):
        category = CashCategory.objects.create(owner=self.user, name="Продажи", kind="income")
        url = reverse("catalog_delete", args=["categories", category.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(CashCategory.objects.exists())
        self.client.post(url)
        self.assertFalse(CashCategory.objects.exists())
