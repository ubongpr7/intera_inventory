from django.core.management.base import BaseCommand

from mainapps.inventory.models import InventoryItem


class Command(BaseCommand):
    help = 'Delete all inventory items'

    def handle(self, *args, **options):
        count = InventoryItem.objects.count()
        InventoryItem.objects.all().delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} inventory items.'))
