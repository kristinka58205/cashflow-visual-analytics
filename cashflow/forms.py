from django import forms
from .models import BankAccount, CashCategory, CashTransaction, Counterparty, CashProject
from decimal import Decimal


class OwnedForm(forms.ModelForm):
    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.owner = owner


class AccountForm(OwnedForm):
    class Meta:
        model = BankAccount
        fields = ["name", "opening_balance"]


class CounterpartyForm(OwnedForm):
    class Meta:
        model = Counterparty
        fields = ["name", "tax_id", "description"]


class ProjectForm(OwnedForm):
    class Meta:
        model = CashProject
        fields = ["name", "code", "description"]


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
        fields = ["account", "category", "counterparty", "project", "occurred_on", "amount", "description"]
        labels = {"account": "Счёт", "category": "Статья"}
        widgets = {"occurred_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = BankAccount.objects.filter(owner=owner)
        self.fields["category"].queryset = CashCategory.objects.filter(owner=owner)
        self.fields["counterparty"].queryset = Counterparty.objects.filter(owner=owner)
        self.fields["project"].queryset = CashProject.objects.filter(owner=owner)


class TransactionFilterForm(forms.Form):
    search = forms.CharField(label="Назначение или контрагент", max_length=100, required=False)
    start = forms.DateField(label="С даты", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="По дату", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    kind = forms.ChoiceField(label="Направление", required=False, choices=[("", "Все направления"), *CashCategory.Kind.choices])
    account = forms.ModelChoiceField(label="Счёт", queryset=BankAccount.objects.none(), required=False)
    category = forms.ModelChoiceField(label="Статья", queryset=CashCategory.objects.none(), required=False)
    counterparty = forms.ModelChoiceField(label="Контрагент", queryset=Counterparty.objects.none(), required=False)
    project = forms.ModelChoiceField(label="Проект", queryset=CashProject.objects.none(), required=False)
    minimum = forms.DecimalField(label="Сумма от, ₽", max_digits=18, decimal_places=2, min_value=Decimal("0.01"), required=False)
    maximum = forms.DecimalField(label="Сумма до, ₽", max_digits=18, decimal_places=2, min_value=Decimal("0.01"), required=False)
    order = forms.ChoiceField(label="Сортировка", required=False, choices=[("newest", "Сначала новые"), ("oldest", "Сначала старые"), ("largest", "Сначала крупные"), ("smallest", "Сначала небольшие")])

    def __init__(self, *args, owner, **kwargs):
        super().__init__(*args, **kwargs)
        for name, model in [("account", BankAccount), ("category", CashCategory), ("counterparty", Counterparty), ("project", CashProject)]:
            self.fields[name].queryset = model.objects.filter(owner=owner)

    def clean(self):
        values = super().clean()
        if values.get("start") and values.get("end") and values["start"] > values["end"]:
            self.add_error("end", "Конец периода должен быть не раньше начала.")
        if values.get("minimum") is not None and values.get("maximum") is not None and values["minimum"] > values["maximum"]:
            self.add_error("maximum", "Верхняя граница должна быть не меньше нижней.")
        return values
