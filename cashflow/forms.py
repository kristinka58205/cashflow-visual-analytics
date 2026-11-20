from django import forms
from .models import BankAccount, CashCategory


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
