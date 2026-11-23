"""Воспроизводимый вымышленный личный учёт для отдельного пустого аккаунта."""
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.utils import timezone
from .access import write_scope
from .audit import record_event
from .models import (BankAccount, CashCategory, Counterparty, CashProject,
                     CashTransaction, ExpenseBudget, PlannedPayment, DemoDataset)


def seed_demo(user):
    with write_scope(user) as scope:
        marker = DemoDataset.objects.filter(user=user).first()
        if marker:
            return marker, False
        if any(scope.catalog(model).exists() for model in
               (BankAccount, CashCategory, Counterparty, CashProject)):
            raise ValidationError("Используйте отдельный пустой аккаунт: личный учёт не изменён.")
        today = timezone.localdate()
        account = BankAccount.objects.create(owner=user, name="Демо: расчётный счёт", opening_balance="100000.00")
        customer = Counterparty.objects.create(owner=user, name="Демо: вымышленный заказчик")
        project = CashProject.objects.create(owner=user, name="Демо: учебный проект", code="DEMO")
        income = CashCategory.objects.create(owner=user, name="Демо: оплата заказов", kind="income")
        expense = CashCategory.objects.create(owner=user, name="Демо: материалы", kind="expense")
        for offset in range(20):
            category = income if offset % 3 == 0 else expense
            entry = CashTransaction(account=account, category=category, counterparty=customer,
                project=project, occurred_on=today-timedelta(days=offset),
                amount=Decimal("15000.25") if category == income else Decimal("1250.10"),
                description=f"Вымышленная демонстрационная операция {offset+1}")
            entry.full_clean()
            entry.save()
            record_event(scope, "created", "operation", entry.pk, entry.description)
        budget = ExpenseBudget.objects.create(owner=user, category=expense,
            month_start=today.replace(day=1), limit="25000.00")
        record_event(scope, "created", "budget", budget.pk, "Демонстрационный месячный бюджет")
        payment = PlannedPayment(account=account, category=expense, due_on=today+timedelta(days=3),
            amount="5000.00", description="Вымышленный будущий платёж")
        payment.full_clean()
        payment.save()
        record_event(scope, "created", "planned_payment", payment.pk, payment.description)
        return DemoDataset.objects.create(user=user, reference_date=today), True
