from django import forms
from .models import BankAccount, CashCategory, CashTransaction, Counterparty, CashProject, ExpenseBudget, PlannedPayment
from decimal import Decimal
from .access import LedgerScope


class OwnedForm(forms.ModelForm):
    def __init__(self, *args, owner, scope=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope or LedgerScope(owner)
        if not self.instance.pk:
            self.instance.owner = owner
            if hasattr(self.instance, "organization_id"):
                self.instance.organization = self.scope.organization


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
        if self.instance.pk and (self.instance.transactions.exists() or self.instance.budgets.exists() or self.instance.planned_payments.exists()):
            original = CashCategory.objects.get(pk=self.instance.pk).kind
            if kind != original:
                raise forms.ValidationError("Направление статьи с операциями менять нельзя: создайте новую статью.")
        return kind


class BudgetForm(OwnedForm):
    month_start = forms.DateField(label="Месяц", input_formats=["%Y-%m", "%Y-%m-%d"], widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}))

    class Meta:
        model = ExpenseBudget
        fields = ["category", "month_start", "limit"]

    def __init__(self, *args, owner, scope=None, **kwargs):
        super().__init__(*args, owner=owner, scope=scope, **kwargs)
        self.fields["category"].queryset = self.scope.catalog(CashCategory).filter(kind="expense")

    def clean(self):
        values = super().clean()
        if values.get("category") and values.get("month_start") and ExpenseBudget.objects.filter(category=values["category"], month_start=values["month_start"]).exclude(pk=self.instance.pk).exists():
            self.add_error("category", "Лимит на эту статью и месяц уже задан.")
        return values


class BudgetMonthForm(forms.Form):
    month = forms.DateField(label="Месяц", input_formats=["%Y-%m"], widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}))


class TransactionForm(forms.ModelForm):
    class Meta:
        model = CashTransaction
        fields = ["account", "category", "counterparty", "project", "occurred_on", "amount", "description"]
        labels = {"account": "Счёт", "category": "Статья"}
        widgets = {"occurred_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}

    def __init__(self, *args, owner, scope=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope or LedgerScope(owner)
        for field, model in [("account", BankAccount), ("category", CashCategory), ("counterparty", Counterparty), ("project", CashProject)]:
            self.fields[field].queryset = self.scope.catalog(model)


class OperationEditForm(TransactionForm):
    expected_revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class OperationCreateForm(TransactionForm):
    command_key = forms.UUIDField(widget=forms.HiddenInput)


class RevisionForm(forms.Form):
    expected_revision = forms.IntegerField(min_value=1,widget=forms.HiddenInput)


class PaymentForm(TransactionForm):
    expected_revision = forms.IntegerField(min_value=1,required=False,widget=forms.HiddenInput)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk:
            self.fields['expected_revision'].required=True
            self.fields['expected_revision'].initial=self.instance.revision

    class Meta:
        model = PlannedPayment
        fields = ["account", "category", "counterparty", "project", "due_on", "amount", "description"]
        widgets = {"due_on": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})}


class PaymentExecutionForm(RevisionForm):
    occurred_on = forms.DateField(label="Фактическая дата", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))


class TransactionFilterForm(forms.Form):
    unassigned = forms.ChoiceField(label="Без аналитики", required=False, choices=[("", "Любые"), ("project", "Без проекта"), ("counterparty", "Без контрагента")])
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

    def __init__(self, *args, owner, scope=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope or LedgerScope(owner)
        for name, model in [("account", BankAccount), ("category", CashCategory), ("counterparty", Counterparty), ("project", CashProject)]:
            self.fields[name].queryset = self.scope.catalog(model)

    def clean(self):
        values = super().clean()
        if values.get("start") and values.get("end") and values["start"] > values["end"]:
            self.add_error("end", "Конец периода должен быть не раньше начала.")
        if values.get("minimum") is not None and values.get("maximum") is not None and values["minimum"] > values["maximum"]:
            self.add_error("maximum", "Верхняя граница должна быть не меньше нижней.")
        return values


class AnalysisPeriodForm(forms.Form):
    start = forms.DateField(label="Начало периода", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))
    end = forms.DateField(label="Конец периода", widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}))

    def clean(self):
        values = super().clean()
        if values.get("start") and values.get("end"):
            days = (values["end"] - values["start"]).days
            if days < 0:
                self.add_error("end", "Конец периода должен быть не раньше начала.")
            elif days > 366:
                self.add_error("end", "Выберите период не длиннее 367 дней.")
        return values
