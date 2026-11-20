from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class RegistrationTests(TestCase):
    def test_register_and_log_in(self):
        response = self.client.post(reverse("register"), {
            "username": "analyst", "password1": "Finance-Review-2048!", "password2": "Finance-Review-2048!",
        })
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), get_user_model().objects.get(username="analyst").pk)

    def test_public_home_has_author(self):
        self.assertContains(self.client.get(reverse("home")), "Жукова Кристина Дмитриевна")
