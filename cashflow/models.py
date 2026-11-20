from django.conf import settings
from django.db import models


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
