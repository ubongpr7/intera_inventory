from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Obsolete: units are now seeded from intera_users common app."

    def handle(self, *args, **options):
        raise CommandError(
            "create_units is obsolete in intera_inventory. Seed units from intera_users with "
            "`./.venv/bin/python manage.py create_units`."
        )
