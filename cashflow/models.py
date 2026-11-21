from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal
from django.core.validators import RegexValidator


class BusinessReference(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField("Название", max_length=120)
    description = models.TextField("Описание", max_length=1000, blank=True)

    class Meta:
        abstract = True
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="%(class)s_unique_owner_name")]

    def __str__(self):
        return self.name


class Counterparty(BusinessReference):
    tax_id = models.CharField("ИНН", max_length=12, blank=True,
        validators=[RegexValidator(r"^(?:[0-9]{10}|[0-9]{12})$", "Укажите 10 или 12 цифр либо оставьте поле пустым.")])


class CashProject(BusinessReference):
    code = models.CharField("Внутренний код", max_length=32, blank=True)


class BankAccount(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField("Название счёта", max_length=120)
    opening_balance = models.DecimalField("Начальный остаток, ₽", max_digits=18, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="account_unique_name_per_owner")]

    def __str__(self):
        return self.name


class CashCategory(models.Model):
    class Kind(models.TextChoices):
        INCOME = "income", "Поступление"
        EXPENSE = "expense", "Расход"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField("Статья", max_length=120)
    kind = models.CharField("Направление", max_length=10, choices=Kind.choices)

    class Meta:
        ordering = ["kind", "name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name", "kind"], name="category_unique_per_owner_kind")]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"


class CashTransaction(models.Model):
    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="transactions")
    category = models.ForeignKey(CashCategory, on_delete=models.PROTECT, related_name="transactions")
    counterparty = models.ForeignKey(Counterparty, verbose_name="Контрагент", on_delete=models.PROTECT, null=True, blank=True, related_name="transactions")
    project = models.ForeignKey(CashProject, verbose_name="Проект", on_delete=models.PROTECT, null=True, blank=True, related_name="transactions")
    occurred_on = models.DateField("Дата операции", default=timezone.localdate)
    amount = models.DecimalField("Сумма, ₽", max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    description = models.CharField("Назначение платежа", max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_on", "-pk"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="cash_transaction_positive_amount")]

    def clean(self):
        if self.account_id and self.category_id:
            account_owner = BankAccount.objects.filter(pk=self.account_id).values_list("owner_id", flat=True).first()
            category_owner = CashCategory.objects.filter(pk=self.category_id).values_list("owner_id", flat=True).first()
            if account_owner != category_owner:
                raise ValidationError("Счёт и статья должны принадлежать одному пользователю.")
            for field, model in [("counterparty", Counterparty), ("project", CashProject)]:
                reference_id = getattr(self, field + "_id")
                if reference_id and not model.objects.filter(pk=reference_id, owner_id=account_owner).exists():
                    raise ValidationError({field: "Выберите запись из собственных справочников."})
        if self.occurred_on and self.occurred_on > timezone.localdate():
            raise ValidationError({"occurred_on": "Будущий платёж относится к плану, а не к фактическим операциям."})


class ExpenseBudget(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    category = models.ForeignKey(CashCategory, verbose_name="Статья расходов", on_delete=models.PROTECT, related_name="budgets")
    month_start = models.DateField("Месяц")
    limit = models.DecimalField("Лимит, ₽", max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "category", "month_start"], name="budget_unique_category_month"),
            models.CheckConstraint(condition=models.Q(limit__gt=0), name="budget_positive_limit")]

    def clean(self):
        if self.category_id and not CashCategory.objects.filter(pk=self.category_id, owner_id=self.owner_id, kind="expense").exists():
            raise ValidationError({"category": "Выберите собственную статью расходов."})
        if self.month_start and self.month_start.day != 1:
            raise ValidationError({"month_start": "Месяц должен быть указан первым числом."})
