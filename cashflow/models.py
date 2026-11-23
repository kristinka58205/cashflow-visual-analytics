from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal
from django.core.validators import RegexValidator
import secrets
from datetime import timedelta


def invitation_token():
    return secrets.token_urlsafe(32)


def invitation_expiry():
    return timezone.now()+timedelta(days=1)


def same_ledger(first, second):
    if first["organization_id"] is not None:
        return first["organization_id"] == second["organization_id"]
    return second["organization_id"] is None and first["owner_id"] == second["owner_id"]


class BusinessReference(models.Model):
    organization = models.ForeignKey("Organization", on_delete=models.PROTECT, null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField("Название", max_length=120)
    description = models.TextField("Описание", max_length=1000, blank=True)

    class Meta:
        abstract = True
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], condition=models.Q(organization__isnull=True), name="%(class)s_personal_name"),
            models.UniqueConstraint(fields=["organization", "name"], condition=models.Q(organization__isnull=False), name="%(class)s_organization_name")]

    def __str__(self):
        return self.name


class Counterparty(BusinessReference):
    tax_id = models.CharField("ИНН", max_length=12, blank=True,
        validators=[RegexValidator(r"^(?:[0-9]{10}|[0-9]{12})$", "Укажите 10 или 12 цифр либо оставьте поле пустым.")])


class CashProject(BusinessReference):
    code = models.CharField("Внутренний код", max_length=32, blank=True)


class BankAccount(models.Model):
    organization = models.ForeignKey("Organization", on_delete=models.PROTECT, null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField("Название счёта", max_length=120)
    opening_balance = models.DecimalField("Начальный остаток, ₽", max_digits=18, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], condition=models.Q(organization__isnull=True), name="account_unique_personal_name"),
            models.UniqueConstraint(fields=["organization", "name"], condition=models.Q(organization__isnull=False), name="account_unique_organization_name")]

    def __str__(self):
        return self.name


class CashCategory(models.Model):
    class Kind(models.TextChoices):
        INCOME = "income", "Поступление"
        EXPENSE = "expense", "Расход"

    organization = models.ForeignKey("Organization", on_delete=models.PROTECT, null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField("Статья", max_length=120)
    kind = models.CharField("Направление", max_length=10, choices=Kind.choices)

    class Meta:
        ordering = ["kind", "name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name", "kind"], condition=models.Q(organization__isnull=True), name="category_unique_personal_name"),
            models.UniqueConstraint(fields=["organization", "name", "kind"], condition=models.Q(organization__isnull=False), name="category_unique_organization_name")]

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
            account = BankAccount.objects.filter(pk=self.account_id).values("owner_id", "organization_id").first()
            category = CashCategory.objects.filter(pk=self.category_id).values("owner_id", "organization_id").first()
            if account and category and not same_ledger(account, category):
                raise ValidationError("Счёт и статья должны относиться к одной рабочей области.")
            for field, model in [("counterparty", Counterparty), ("project", CashProject)]:
                reference_id = getattr(self, field + "_id")
                reference = model.objects.filter(pk=reference_id).values("owner_id", "organization_id").first() if reference_id else None
                if account and reference and not same_ledger(account, reference):
                    raise ValidationError({field: "Выберите запись из той же рабочей области."})
        if self.occurred_on and self.occurred_on > timezone.localdate():
            raise ValidationError({"occurred_on": "Будущий платёж относится к плану, а не к фактическим операциям."})


class ExpenseBudget(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    category = models.ForeignKey(CashCategory, verbose_name="Статья расходов", on_delete=models.PROTECT, related_name="budgets")
    month_start = models.DateField("Месяц")
    limit = models.DecimalField("Лимит, ₽", max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["category", "month_start"], name="budget_unique_category_month"),
            models.CheckConstraint(condition=models.Q(limit__gt=0), name="budget_positive_limit")]

    def clean(self):
        if self.category_id:
            category = CashCategory.objects.filter(pk=self.category_id, kind="expense").first()
            if not category or (category.organization_id is None and category.owner_id != self.owner_id):
                raise ValidationError({"category": "Выберите статью расходов своей рабочей области."})
        if self.month_start and self.month_start.day != 1:
            raise ValidationError({"month_start": "Месяц должен быть указан первым числом."})


class PlannedPayment(models.Model):
    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="planned_payments")
    category = models.ForeignKey(CashCategory, on_delete=models.PROTECT, related_name="planned_payments")
    counterparty = models.ForeignKey(Counterparty, on_delete=models.PROTECT, null=True, blank=True)
    project = models.ForeignKey(CashProject, on_delete=models.PROTECT, null=True, blank=True)
    due_on = models.DateField("Срок платежа")
    amount = models.DecimalField("Сумма, ₽", max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    description = models.CharField("Назначение", max_length=500)
    status = models.CharField(max_length=10, choices=[("planned", "В плане"), ("paid", "Оплачен"), ("cancelled", "Отменён")], default="planned")
    transaction = models.OneToOneField(CashTransaction, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        ordering = ["due_on", "pk"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="planned_payment_positive"),
            models.CheckConstraint(condition=(models.Q(status="paid", transaction__isnull=False) | models.Q(status__in=["planned", "cancelled"], transaction__isnull=True)), name="planned_payment_consistent")]

    def clean(self):
        CashTransaction(account_id=self.account_id, category_id=self.category_id,
            counterparty_id=self.counterparty_id, project_id=self.project_id).clean()


class ImportBatch(models.Model):
    organization = models.ForeignKey("Organization", on_delete=models.PROTECT, null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    filename = models.CharField(max_length=120)
    sha256 = models.CharField(max_length=64)
    rows = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "sha256"], condition=models.Q(organization__isnull=True), name="import_unique_personal_file"),
            models.UniqueConstraint(fields=["organization", "sha256"], condition=models.Q(organization__isnull=False), name="import_unique_organization_file")]


class Organization(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_finance_organizations")
    name = models.CharField("Название организации", max_length=120)
    legal_name = models.CharField("Полное наименование", max_length=250, blank=True)
    tax_id = models.CharField("ИНН", max_length=12, blank=True,
        validators=[RegexValidator(r"^(?:[0-9]{10}|[0-9]{12})$", "Укажите 10 или 12 цифр либо оставьте поле пустым.")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="organization_unique_owner_name")]

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Администратор"
        EDITOR = "editor", "Финансовый специалист"
        VIEWER = "viewer", "Руководитель (просмотр)"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="finance_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.VIEWER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "user"], name="organization_unique_member")]


class OrganizationInvitation(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="finance_invitations_created")
    token = models.CharField(max_length=64, unique=True, default=invitation_token, editable=False)
    role = models.CharField(max_length=10, choices=[("editor", "Финансовый специалист"), ("viewer", "Руководитель (просмотр)")], default="viewer")
    expires_at = models.DateTimeField(default=invitation_expiry)
    revoked = models.BooleanField(default=False)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="finance_invitations_accepted")
    accepted_at = models.DateTimeField(null=True, blank=True)


class LedgerEvent(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Создание"
        UPDATED = "updated", "Изменение"
        DELETED = "deleted", "Удаление"
        PAID = "paid", "Проведение"
        CANCELLED = "cancelled", "Отмена"
        IMPORTED = "imported", "Импорт"

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    actor_name = models.CharField(max_length=150)
    action = models.CharField(max_length=12, choices=Action.choices)
    entity = models.CharField(max_length=24, choices=[("catalog", "Справочник"), ("operation", "Операция"), ("budget", "Бюджет"), ("planned_payment", "Плановый платёж"), ("import", "Загрузка CSV"), ("receipt", "Документ операции"), ("reconciliation", "Сверка остатков"), ("transfer", "Межсчётный перевод")])
    object_id = models.PositiveBigIntegerField()
    summary = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class Feedback(models.Model):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    subject = models.CharField("Тема", max_length=160)
    email = models.EmailField("Почта для ответа", blank=True)
    message = models.TextField("Сообщение", max_length=4000)
    status = models.CharField("Статус", max_length=12, default="new", choices=[("new", "Новое"), ("review", "В работе"), ("closed", "Закрыто")])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class TransactionReceipt(models.Model):
    transaction = models.ForeignKey(CashTransaction, on_delete=models.PROTECT, related_name="receipts")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    filename = models.CharField(max_length=120)
    content = models.BinaryField()
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["transaction", "sha256"], name="receipt_unique_transaction_content")]


class DemoDataset(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    reference_date = models.DateField()
    version = models.CharField(max_length=20, default="finance-demo-v1")


class AccountReconciliation(models.Model):
    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT)
    checked_on = models.DateField("Дата остатка")
    statement_balance = models.DecimalField("Остаток по выписке, ₽", max_digits=18, decimal_places=2)
    ledger_balance = models.DecimalField(max_digits=24, decimal_places=2)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["account", "checked_on", "statement_balance", "ledger_balance"], name="unique_account_reconciliation_snapshot")]


class CashTransfer(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    from_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="outgoing_transfers")
    to_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="incoming_transfers")
    amount = models.DecimalField("Сумма, ₽", max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    occurred_on = models.DateField("Дата", default=timezone.localdate)
    description = models.CharField("Назначение", max_length=500)
    command_key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_on", "-pk"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="transfer_positive"),
            models.CheckConstraint(condition=~models.Q(from_account=models.F("to_account")), name="transfer_different_accounts")]

    def clean(self):
        if self.from_account_id == self.to_account_id:
            raise ValidationError("Выберите два разных счёта.")
        if self.from_account_id and self.to_account_id:
            first = BankAccount.objects.filter(pk=self.from_account_id).values("owner_id", "organization_id").first()
            second = BankAccount.objects.filter(pk=self.to_account_id).values("owner_id", "organization_id").first()
            if first and second and not same_ledger(first, second):
                raise ValidationError("Оба счёта должны принадлежать одной рабочей области.")
        if self.occurred_on and self.occurred_on > timezone.localdate():
            raise ValidationError("Будущий перевод нельзя отразить как фактический.")
