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

from cashflow import receipts

from cashflow.cabinet import cabinet, account_action

from cashflow.documents import cashflow_pdf

from cashflow.reconciliation import reconciliation_list

from cashflow.forecast import forecast

from cashflow.transfers import transfer_list

from cashflow.statements import statement_view

from cashflow.dimensions import dimensions

urlpatterns = [
    path("analytics/dimensions/", dimensions, name="dimensions"),
    path("statement/",statement_view,name="statement"),
    path("transfers/", transfer_list, name="transfer_list"),
    path("forecast/", forecast, name="forecast"),
    path("reconciliation/", reconciliation_list, name="reconciliation_list"),
    path("reports/cashflow.pdf", cashflow_pdf, name="cashflow_pdf"),
    path("cabinet/", cabinet, name="cabinet_home"),
    path("cabinet/accounts/action/", account_action, name="account_action"),
    path("cabinet/feedback/", feedback_queue, name="feedback_queue"),
    path("cabinet/<slug:section>/", cabinet, name="cabinet"),
    path("operations/<int:pk>/receipts/", receipts.receipt_list, name="receipt_list"),
    path("operations/<int:pk>/receipts/upload/", receipts.receipt_upload, name="receipt_upload"),
    path("receipts/<int:pk>/download/", receipts.receipt_download, name="receipt_download"),
    path("receipts/<int:pk>/delete/", receipts.receipt_delete, name="receipt_delete"),
    path("feedback/", feedback, name="feedback"),
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
