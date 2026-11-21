from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Organization, OrganizationMembership
from .organizations import create_organization


class OrganizationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("org-owner")
        self.other = get_user_model().objects.create_user("org-other")
        self.client.force_login(self.owner)

    def test_create_and_retry_keep_one_owner_and_membership(self):
        payload = {"name": "Предприятие", "owner": self.other.pk, "tax_id": "1234567890"}
        self.client.post(reverse("organization_create"), payload)
        self.client.post(reverse("organization_create"), payload)
        organization = Organization.objects.get()
        self.assertEqual(organization.owner, self.owner)
        self.assertEqual(organization.memberships.count(), 1)
        self.assertEqual(organization.memberships.get().role, "admin")

    def test_membership_and_role_guard_details_and_edit(self):
        organization = create_organization(self.owner, name="Закрытая организация")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("organization_detail", args=[organization.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("organization_list")), organization.name)
        OrganizationMembership.objects.create(organization=organization, user=self.other, role="viewer")
        self.assertContains(self.client.get(reverse("organization_detail", args=[organization.pk])), organization.name)
        self.assertEqual(self.client.post(reverse("organization_edit", args=[organization.pk]), {"name": "Подмена"}).status_code, 403)
