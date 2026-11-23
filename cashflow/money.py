"""Точные суммы в SQLite (текст + decimal collation) и PostgreSQL numeric."""
from decimal import Decimal, localcontext
from django.db import models
from django.db.backends.signals import connection_created
from django.dispatch import receiver


class CashDecimalField(models.DecimalField):
    def get_internal_type(self):
        # Не включать SQLite-конвертер float -> Decimal с точностью 15 цифр.
        return "CashDecimalField"

    def db_type(self, connection):
        if connection.vendor == "sqlite":
            return "text COLLATE CASH_DECIMAL"
        column_type = connection.data_types["DecimalField"]
        params = self.db_type_parameters(connection)
        return column_type(params) if callable(column_type) else column_type % params

    def get_db_prep_value(self, value, connection, prepared=False):
        value = self.to_python(value)
        if value is None:
            return None
        if connection.vendor == "sqlite":
            return format(value, "f")
        return connection.ops.adapt_decimalfield_value(value, self.max_digits, self.decimal_places)

    def from_db_value(self, value, expression, connection):
        return None if value is None else Decimal(str(value))


class DecimalAccumulator:
    def __init__(self):
        self.total = Decimal(0)
        self.present = False

    def step(self, value):
        if value is not None:
            with localcontext() as ctx:
                ctx.prec = 50
                self.total += Decimal(str(value))
            self.present = True

    def finalize(self):
        return format(self.total, "f") if self.present else None


def decimal_compare(first, second):
    left, right = Decimal(first), Decimal(second)
    return (left > right) - (left < right)


@receiver(connection_created, dispatch_uid="cashflow.exact_money")
def configure_money_connection(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        connection.connection.create_collation("CASH_DECIMAL", decimal_compare)
        connection.connection.create_aggregate("CASH_SUM", 1, DecimalAccumulator)


class CashSum(models.Sum):
    def as_sqlite(self, compiler, connection, **extra_context):
        return self.as_sql(compiler, connection, function="CASH_SUM", **extra_context)
