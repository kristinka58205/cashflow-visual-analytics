from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal


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
        if self.occurred_on and self.occurred_on > timezone.localdate():
            raise ValidationError({"occurred_on": "Будущий платёж относится к плану, а не к фактическим операциям."})
