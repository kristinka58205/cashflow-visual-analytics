"""Проверяет версию открытой формы до финансового изменения."""
from django.core.exceptions import ValidationError


def require_revision(instance,expected):
    if type(expected) is not int or expected!=instance.revision:
        raise ValidationError('Запись уже изменилась в другой вкладке. Откройте актуальные данные заново.')
