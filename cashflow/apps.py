from django.apps import AppConfig


class CashflowConfig(AppConfig):
    name = 'cashflow'

    def ready(self):
        from . import money  # noqa: F401 — регистрация SQLite-функций на каждом соединении
