"""Атомарный ввод фактической операции с постоянной квитанцией повтора."""
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from .access import write_scope
from .audit import record_event
from .models import BankAccount, CashCategory, CashProject, Counterparty, OperationCommand


def create_operation(user, scope, command_key, entry):
    with write_scope(user, scope) as scope:
        for field, model in [('account', BankAccount), ('category', CashCategory),
                             ('counterparty', Counterparty), ('project', CashProject)]:
            value = getattr(entry, field)
            if value is not None:
                get_object_or_404(scope.catalog(model), pk=value.pk)
        payload = {field: getattr(entry, field + '_id')
                   for field in ['account', 'category', 'counterparty', 'project']}
        payload.update(occurred_on=entry.occurred_on.isoformat(),
                       amount=f'{entry.amount:.2f}', description=entry.description)
        previous = OperationCommand.objects.filter(key=command_key).first()
        if previous:
            if (previous.owner_id != user.pk or previous.organization_id != scope.organization_id
                    or previous.payload != payload):
                raise ValidationError('Ключ операции уже использован с другими данными.')
            if previous.entry_id is None:
                raise ValidationError('Эта операция уже была записана и затем удалена. Для нового ввода откройте новую форму.')
            item = get_object_or_404(scope.entries(), pk=previous.entry_id)
            return item, False
        entry.full_clean()
        entry.save()
        record_event(scope, 'created', 'operation', entry.pk, f'{entry.description}: {entry.amount:.2f} ₽')
        OperationCommand.objects.create(owner=user, organization=scope.organization,
                                        key=command_key, payload=payload, entry=entry)
        return entry, True
