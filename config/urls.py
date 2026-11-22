from django.contrib import admin
from django.urls import include, path
from cashflow import views
from cashflow.exports import transaction_export
from cashflow import budgets
from cashflow import payments
from cashflow import imports
from cashflow import organizations
from cashflow.access import workspace_select
from cashflow.audit import audit_list, audit_export

from cashflow.public import guide

from cashflow.feedback import feedback, feedback_queue

urlpatterns = [
    path("feedback/", feedback, name="feedback"),
    path("cabinet/feedback/", feedback_queue, name="feedback_queue"),
    path("guide/<slug:slug>/", guide, name="guide"),
    path("admin/", admin.site.urls),
    path("accounts/register/", views.register, name="register"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", views.home, name="home"),
    path("analytics/", views.analytics, name="analytics"),
    path("history/", audit_list, name="audit_list"),
    path("history/export/", audit_export, name="audit_export"),
    path("workspace/<int:pk>/", workspace_select, name="workspace_select"),
    path("organizations/", organizations.organization_list, name="organization_list"),
    path("organizations/new/", organizations.organization_create, name="organization_create"),
    path("organizations/join/", organizations.organization_join, name="organization_join"),
    path("organizations/<int:pk>/", organizations.organization_detail, name="organization_detail"),
    path("organizations/<int:pk>/edit/", organizations.organization_edit, name="organization_edit"),
    path("organizations/<int:pk>/invite/", organizations.invitation_create, name="invitation_create"),
    path("organizations/<int:pk>/invite/<int:invitation_pk>/revoke/", organizations.invitation_revoke, name="invitation_revoke"),
    path("organizations/<int:pk>/member/<int:member_pk>/", organizations.member_change, name="member_change"),
    path("organizations/<int:pk>/leave/", organizations.organization_leave, name="organization_leave"),
    path("imports/", imports.import_upload, name="import_upload"),
    path("imports/<int:pk>/", imports.import_preview, name="import_preview"),
    path("payments/", payments.payment_list, name="payment_list"),
    path("payments/new/", payments.payment_edit, name="payment_create"),
    path("payments/<int:pk>/edit/", payments.payment_edit, name="payment_edit"),
    path("payments/<int:pk>/execute/", payments.payment_execute, name="payment_execute"),
    path("payments/<int:pk>/cancel/", payments.payment_cancel, name="payment_cancel"),
    path("budgets/", budgets.budget_list, name="budget_list"),
    path("budgets/new/", budgets.budget_edit, name="budget_create"),
    path("budgets/<int:pk>/edit/", budgets.budget_edit, name="budget_edit"),
    path("budgets/<int:pk>/delete/", budgets.budget_delete, name="budget_delete"),
    path("catalog/", views.catalog, name="catalog"),
    path("catalog/<str:kind>/new/", views.catalog_edit, name="catalog_create"),
    path("catalog/<str:kind>/<int:pk>/edit/", views.catalog_edit, name="catalog_edit"),
    path("catalog/<str:kind>/<int:pk>/delete/", views.catalog_delete, name="catalog_delete"),
    path("operations/", views.transaction_list, name="transaction_list"),
    path("operations/export/", transaction_export, name="transaction_export"),
    path("operations/new/", views.transaction_edit, name="transaction_create"),
    path("operations/<int:pk>/edit/", views.transaction_edit, name="transaction_edit"),
    path("operations/<int:pk>/delete/", views.transaction_delete, name="transaction_delete"),
]
