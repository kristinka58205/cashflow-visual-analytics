from django import forms
from .models import BankAccount, CashCategory, CashTransaction


class OwnedForm(forms.ModelForm):
    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner = owner


class AccountForm(OwnedForm):
    class Meta:
        model = BankAccount
        fields = ["name", "opening_balance"]


class CategoryForm(OwnedForm):
    class Meta:
        model = CashCategory
        fields = ["name", "kind"]

    def clean_kind(self):
        kind = self.cleaned_data["kind"]
        if self.instance.pk and self.instance.transactions.exists():
            original = CashCategory.objects.get(pk=self.instance.pk).kind
            if kind != original:
                raise forms.ValidationError("Направление статьи с операциями менять нельзя: создайте новую статью.")
        return kind


class TransactionForm(forms.ModelForm):
    class Meta:
        model = CashTransaction
        fields = ["account", "category", "occurred_on", "amount", "description"]
        labels = {"account": "Счёт", "category": "Статья"}
        widgets = {"occurred_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = BankAccount.objects.filter(owner=owner)
        self.fields["category"].queryset = CashCategory.objects.filter(owner=owner)
