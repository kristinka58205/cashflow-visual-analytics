from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import OrganizationInvitation, OrganizationMembership
from .organizations import accept_invitation, create_organization


class InvitationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("inviter")
        self.user = get_user_model().objects.create_user("invitee")
        self.other = get_user_model().objects.create_user("second-invitee")
        self.organization = create_organization(self.owner, name="Команда")
        self.invitation = OrganizationInvitation.objects.create(organization=self.organization, created_by=self.owner, role="editor")

    def test_invitation_is_single_use_and_retry_does_not_restore_removed_access(self):
        first = accept_invitation(self.user, self.invitation.token)
        self.assertEqual(accept_invitation(self.user, self.invitation.token).pk, first.pk)
        self.assertEqual(first.role, "editor")
        with self.assertRaises(ValidationError):
            accept_invitation(self.other, self.invitation.token)
        first.delete()
        with self.assertRaises(ValidationError):
            accept_invitation(self.user, self.invitation.token)

    def test_expired_revoked_and_non_admin_actions_are_rejected(self):
        self.invitation.expires_at = timezone.now()-timedelta(seconds=1)
        self.invitation.save()
        with self.assertRaises(ValidationError):
            accept_invitation(self.user, self.invitation.token)
        OrganizationMembership.objects.create(organization=self.organization, user=self.user, role="viewer")
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse("invitation_create", args=[self.organization.pk]), {"role": "editor"}).status_code, 403)
        self.assertNotContains(self.client.get(reverse("organization_detail", args=[self.organization.pk])), self.invitation.token)

    def test_owner_cannot_be_demoted_and_admin_can_remove_other_member(self):
        member = accept_invitation(self.user, self.invitation.token)
        self.client.force_login(self.owner)
        owner_member = self.organization.memberships.get(user=self.owner)
        self.client.post(reverse("member_change", args=[self.organization.pk, owner_member.pk]), {"action": "remove"})
        self.assertTrue(self.organization.memberships.filter(user=self.owner, role="admin").exists())
        self.client.post(reverse("member_change", args=[self.organization.pk, member.pk]), {"action": "remove"})
        self.assertFalse(self.organization.memberships.filter(user=self.user).exists())
