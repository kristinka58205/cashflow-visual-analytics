"""Организации и команды: доступ определяется членством, а не is_staff."""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from .models import Organization, OrganizationMembership


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
    return render(request, "cashflow/organizations.html", {"memberships": request.user.finance_memberships.select_related("organization")})


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
        "organization": membership.organization, "members": membership.organization.memberships.select_related("user")})


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
