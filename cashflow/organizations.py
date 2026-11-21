"""Организации и команды: доступ определяется членством, а не is_staff."""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from .models import Organization, OrganizationMembership, OrganizationInvitation
from django.utils import timezone
from django.views.decorators.http import require_POST


class InvitationForm(forms.ModelForm):
    class Meta:
        model = OrganizationInvitation
        fields = ["role"]
        labels = {"role": "Роль нового участника"}


class JoinOrganizationForm(forms.Form):
    token = forms.CharField(label="Код приглашения", max_length=64)


@transaction.atomic
def accept_invitation(user, token):
    invitation = OrganizationInvitation.objects.filter(token=token).first()
    if not invitation:
        raise ValidationError("Приглашение недействительно.")
    Organization.objects.select_for_update().get(pk=invitation.organization_id)
    invitation.refresh_from_db()
    existing = OrganizationMembership.objects.filter(organization=invitation.organization, user=user).first()
    if invitation.accepted_at:
        if invitation.accepted_by_id == user.pk and existing:
            return existing
        raise ValidationError("Приглашение недействительно.")
    if invitation.revoked or invitation.expires_at <= timezone.now():
        raise ValidationError("Приглашение недействительно.")
    if existing:
        return existing
    membership = OrganizationMembership.objects.create(organization=invitation.organization, user=user, role=invitation.role)
    invitation.accepted_by, invitation.accepted_at = user, timezone.now()
    invitation.save(update_fields=["accepted_by", "accepted_at"])
    return membership


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = ["name", "legal_name", "tax_id"]


def membership_for(user, pk, admin=False, lock=False):
    organization = get_object_or_404(Organization.objects.select_for_update() if lock else Organization.objects.all(), pk=pk)
    member = get_object_or_404(OrganizationMembership, organization=organization, user=user)
    if admin and member.role != OrganizationMembership.Role.ADMIN:
        raise PermissionDenied("Управление организацией доступно администратору команды.")
    return member


@transaction.atomic
def create_organization(user, **values):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    organization, created = Organization.objects.get_or_create(owner=user, name=values["name"],
        defaults={"legal_name": values.get("legal_name", ""), "tax_id": values.get("tax_id", "")})
    if created:
        OrganizationMembership.objects.create(organization=organization, user=user, role="admin")
    return organization


@login_required
def organization_list(request):
    return render(request, "cashflow/organizations.html", {"memberships": request.user.finance_memberships.select_related("organization"), "join_form": JoinOrganizationForm()})


@login_required
def organization_create(request):
    form = OrganizationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        organization = create_organization(request.user, **form.cleaned_data)
        return redirect("organization_detail", pk=organization.pk)
    return render(request, "cashflow/organization_form.html", {"form": form})


@login_required
def organization_detail(request, pk):
    membership = membership_for(request.user, pk)
    return render(request, "cashflow/organization_detail.html", {"membership": membership,
        "organization": membership.organization, "members": membership.organization.memberships.select_related("user"),
        "invitation_form": InvitationForm(), "roles": OrganizationMembership.Role.choices,
        "invitations": membership.organization.invitations.filter(accepted_at__isnull=True, revoked=False, expires_at__gt=timezone.now()) if membership.role == "admin" else []})


@login_required
@transaction.atomic
def organization_edit(request, pk):
    membership = membership_for(request.user, pk, admin=True, lock=True)
    form = OrganizationForm(request.POST if request.method == "POST" else None, instance=membership.organization)
    if request.method == "POST" and form.is_valid():
        if Organization.objects.filter(owner=membership.organization.owner, name=form.cleaned_data["name"]).exclude(pk=pk).exists():
            form.add_error("name", "Такое название уже используется.")
        else:
            form.save()
            return redirect("organization_detail", pk=pk)
    return render(request, "cashflow/organization_form.html", {"form": form})


@login_required
@require_POST
@transaction.atomic
def invitation_create(request, pk):
    member = membership_for(request.user, pk, admin=True, lock=True)
    form = InvitationForm(request.POST)
    if form.is_valid():
        invitation = form.save(commit=False)
        invitation.organization, invitation.created_by = member.organization, request.user
        invitation.save()
    else:
        messages.error(request, "Выберите роль из списка приглашения.")
    return redirect("organization_detail", pk=pk)


@login_required
@require_POST
def organization_join(request):
    form = JoinOrganizationForm(request.POST)
    if form.is_valid():
        try:
            member = accept_invitation(request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error("token", exc)
        else:
            return redirect("organization_detail", pk=member.organization_id)
    return render(request, "cashflow/organizations.html", {"memberships": request.user.finance_memberships.select_related("organization"), "join_form": form})


@login_required
@require_POST
@transaction.atomic
def invitation_revoke(request, pk, invitation_pk):
    member = membership_for(request.user, pk, admin=True, lock=True)
    invitation = get_object_or_404(OrganizationInvitation, organization=member.organization, pk=invitation_pk)
    invitation.revoked = True
    invitation.save(update_fields=["revoked"])
    return redirect("organization_detail", pk=pk)


@login_required
@require_POST
@transaction.atomic
def member_change(request, pk, member_pk):
    admin = membership_for(request.user, pk, admin=True, lock=True)
    member = get_object_or_404(OrganizationMembership, organization=admin.organization, pk=member_pk)
    if member.user_id == admin.organization.owner_id:
        messages.error(request, "Владелец сохраняет административный доступ.")
    elif request.POST.get("action") == "remove":
        member.delete()
    elif request.POST.get("role") in dict(OrganizationMembership.Role.choices):
        member.role = request.POST["role"]
        member.save(update_fields=["role"])
    return redirect("organization_detail", pk=pk)


@login_required
@require_POST
@transaction.atomic
def organization_leave(request, pk):
    member = membership_for(request.user, pk, lock=True)
    if member.organization.owner_id == request.user.pk:
        messages.error(request, "Владелец не может покинуть свою организацию.")
    else:
        member.delete()
    return redirect("organization_list")
