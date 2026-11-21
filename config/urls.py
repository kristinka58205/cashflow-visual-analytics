from django.contrib import admin
from django.urls import include, path
from cashflow import views
from cashflow.exports import transaction_export
from cashflow import budgets

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/register/", views.register, name="register"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", views.home, name="home"),
    path("analytics/", views.analytics, name="analytics"),
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
