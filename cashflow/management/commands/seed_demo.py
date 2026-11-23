from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from cashflow.demo import seed_demo


class Command(BaseCommand):
    help = "Создать вымышленные данные в отдельном пустом аккаунте"

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True)

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(username=options["user"], is_active=True).first()
        if user is None:
            raise CommandError("Сначала зарегистрируйте отдельный активный аккаунт.")
        try:
            marker, created = seed_demo(user)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"{'Создан' if created else 'Уже существует'} демонабор от {marker.reference_date}. Все операции вымышлены.")
