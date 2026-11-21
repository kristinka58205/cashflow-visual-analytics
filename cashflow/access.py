"""Единая область учёта: личная или организация с действующим членством."""
from contextlib import contextmanager
from functools import wraps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from .models import Organization, OrganizationMembership, BankAccount, CashCategory, Counterparty, CashProject, CashTransaction, PlannedPayment, ExpenseBudget, ImportBatch, LedgerEvent


class LedgerScope:
    def __init__(self, user, organization=None):
        self.user, self.organization = user, organization
        self.role = "admin"
        if organization is not None:
            member = OrganizationMembership.objects.filter(user=user, organization=organization).first()
            if member is None:
                raise PermissionDenied("Доступ к выбранной организации закрыт. Выберите другую рабочую область.")
            self.role = member.role

    @property
    def organization_id(self):
        return self.organization.pk if self.organization else None

    @property
    def can_write(self):
        return self.role in {"admin", "editor"}

    @property
    def name(self):
        return self.organization.name if self.organization else "Личный учёт"

    @property
    def role_label(self):
        return dict(OrganizationMembership.Role.choices)[self.role] if self.organization else "Владелец"

    def catalog(self, model):
        if self.organization:
            return model.objects.filter(organization=self.organization)
        return model.objects.filter(owner=self.user, organization__isnull=True)

    def entries(self):
        query = CashTransaction.objects.all()
        return query.filter(account__organization=self.organization) if self.organization else query.filter(account__owner=self.user, account__organization__isnull=True)

    def payments(self):
        query = PlannedPayment.objects.all()
        return query.filter(account__organization=self.organization) if self.organization else query.filter(account__owner=self.user, account__organization__isnull=True)

    def budgets(self):
        query = ExpenseBudget.objects.all()
        return query.filter(category__organization=self.organization) if self.organization else query.filter(owner=self.user, category__owner=self.user, category__organization__isnull=True)

    def imports(self):
        return self.catalog(ImportBatch)

    def events(self):
        return LedgerEvent.objects.filter(organization=self.organization) if self.organization else LedgerEvent.objects.filter(organization__isnull=True, actor=self.user)


def get_scope(request):
    if not hasattr(request, "_ledger_scope"):
        pk = request.session.get("finance_organization_id")
        if pk is not None and (type(pk) is not int or pk <= 0):
            raise PermissionDenied("Выберите рабочую область заново.")
        organization = Organization.objects.filter(pk=pk).first() if pk else None
        if pk and organization is None:
            raise PermissionDenied("Организация больше недоступна. Выберите рабочую область заново.")
        request._ledger_scope = LedgerScope(request.user, organization)
    return request._ledger_scope


@contextmanager
def write_scope(user, scope=None):
    scope = scope or LedgerScope(user)
    if scope.user.pk != user.pk:
        raise PermissionDenied
    with transaction.atomic():
        # Изменения состава команды также сначала блокируют организацию.
        if scope.organization:
            organization = Organization.objects.select_for_update().get(pk=scope.organization_id)
            scope = LedgerScope(user, organization)
        get_user_model().objects.select_for_update().get(pk=user.pk)
        if not scope.can_write:
            raise PermissionDenied("Роль руководителя позволяет просматривать данные и отчёты.")
        yield scope


def financial_write(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        with write_scope(request.user, get_scope(request)) as scope:
            request._ledger_scope = scope
            return view(request, *args, **kwargs)
    return wrapped


def workspace_context(request):
    if not request.user.is_authenticated:
        return {}
    try:
        return {"ledger": get_scope(request)}
    except PermissionDenied:
        return {"ledger_access_lost": True}


@login_required
@require_POST
def workspace_select(request, pk):
    organization = None
    if pk:
        organization = Organization.objects.filter(pk=pk).first()
        if organization is None:
            raise PermissionDenied
        LedgerScope(request.user, organization)
    if organization:
        request.session["finance_organization_id"] = organization.pk
    else:
        request.session.pop("finance_organization_id", None)
    return redirect("home")
