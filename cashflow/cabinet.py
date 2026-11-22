from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .feedback import staff_only
from .models import Feedback, Organization, OrganizationMembership

PAGES = [("overview", "Обзор сервиса"), ("users", "Аккаунты"), ("organizations", "Организации"), ("memberships", "Участники"), ("integrity", "Целостность команд")]


class AccountAction(forms.Form):
    user_id = forms.IntegerField(min_value=1)
    action = forms.ChoiceField(choices=[("activate", "Включить"), ("deactivate", "Отключить")])


@user_passes_test(staff_only)
def cabinet(request, section="overview"):
    if section not in dict(PAGES):
        raise Http404
    users = get_user_model().objects.all()
    search = request.GET.get("q", "").strip()[:120]
    rows = []
    if section == "overview":
        rows = [("Аккаунты", users.count()), ("Активные", users.filter(is_active=True).count()), ("Организации", Organization.objects.count()), ("Участники", OrganizationMembership.objects.count()), ("Новые обращения", Feedback.objects.filter(status="new").count())]
    elif section == "users":
        query = users.order_by("pk")
        if search:
            query = query.filter(username__icontains=search)
        rows = [{"pk":u.pk,"title":u.get_username(),"detail":"Активен" if u.is_active else "Отключён", "actionable":not (u.is_staff or u.is_superuser)} for u in Paginator(query,50).get_page(request.GET.get("page"))]
    elif section == "organizations":
        query = Organization.objects.select_related("owner").order_by("pk")
        if search:
            query = query.filter(name__icontains=search)
        rows = [{"title":o.name,"detail":f"Владелец: {o.owner.get_username()} · ID {o.pk}"} for o in Paginator(query,50).get_page(request.GET.get("page"))]
    elif section == "memberships":
        query = OrganizationMembership.objects.select_related("user","organization").order_by("pk")
        if search:
            query = query.filter(Q(user__username__icontains=search)|Q(organization__name__icontains=search))
        rows = [{"title":f"{m.user.get_username()} · {m.organization.name}","detail":m.get_role_display()} for m in Paginator(query,50).get_page(request.GET.get("page"))]
    else:
        owners = OrganizationMembership.objects.filter(organization_id=OuterRef("pk"),user_id=OuterRef("owner_id"),role="admin")
        query = Organization.objects.annotate(owner_has_admin=Exists(owners)).filter(owner_has_admin=False).order_by("pk")
        rows = [{"title":o.name,"detail":"Отсутствует административное членство владельца; требуется проверка. Автоматическое исправление не выполняется."} for o in Paginator(query,50).get_page(request.GET.get("page"))]
    return render(request,"cashflow/cabinet.html",{"section":section,"heading":dict(PAGES)[section],"navigation":PAGES,"rows":rows,"q":search,"page_number":request.GET.get("page","1")})


@user_passes_test(staff_only)
@require_POST
def account_action(request):
    form = AccountAction(request.POST)
    if not form.is_valid():
        return render(request,"cashflow/cabinet.html",{"heading":"Некорректное действие","navigation":PAGES},status=400)
    with transaction.atomic():
        user = get_object_or_404(get_user_model().objects.select_for_update(),pk=form.cleaned_data["user_id"],is_staff=False,is_superuser=False)
        user.is_active = form.cleaned_data["action"] == "activate"
        user.save(update_fields=["is_active"])
    return redirect("cabinet", section="users")
