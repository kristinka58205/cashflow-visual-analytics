from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models.deletion import ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from .forms import AccountForm, CategoryForm
from .models import BankAccount, CashCategory


def home(request):
    return render(request, "cashflow/home.html")


def register(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = UserCreationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save())
        return redirect("home")
    return render(request, "registration/register.html", {"form": form})


@login_required
def catalog(request):
    return render(request, "cashflow/catalog.html", {
        "accounts": BankAccount.objects.filter(owner=request.user),
        "categories": CashCategory.objects.filter(owner=request.user),
    })


def catalog_type(kind):
    if kind == "accounts":
        return BankAccount, AccountForm, "Счёт"
    if kind == "categories":
        return CashCategory, CategoryForm, "Статья"
    raise Http404


@login_required
def catalog_edit(request, kind, pk=None):
    model, form_class, title = catalog_type(kind)
    instance = get_object_or_404(model, owner=request.user, pk=pk) if pk else model(owner=request.user)
    form = form_class(request.POST if request.method == "POST" else None, owner=request.user, instance=instance)
    if request.method == "POST" and form.is_valid():
        # Проверка уникальности с owner нужна до записи; owner не является полем формы.
        duplicates = model.objects.filter(owner=request.user, name=form.cleaned_data["name"])
        if model is CashCategory:
            duplicates = duplicates.filter(kind=form.cleaned_data["kind"])
        if duplicates.exclude(pk=instance.pk).exists():
            form.add_error("name", "Такое название уже используется.")
        else:
            form.save()
            messages.success(request, "Изменения сохранены.")
            return redirect("catalog")
    return render(request, "cashflow/catalog_form.html", {"form": form, "heading": title})


@login_required
def catalog_delete(request, kind, pk):
    model, _, _ = catalog_type(kind)
    instance = get_object_or_404(model, owner=request.user, pk=pk)
    if request.method == "POST":
        try:
            instance.delete()
        except ProtectedError:
            messages.error(request, "Справочник используется в операциях и не может быть удалён.")
        else:
            messages.success(request, "Запись удалена.")
        return redirect("catalog")
    return render(request, "cashflow/confirm_delete.html", {"instance": instance})
