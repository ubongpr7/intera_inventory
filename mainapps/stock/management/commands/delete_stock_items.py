from django.core.management.base import BaseCommand
from ...models import StockItem

class Command(BaseCommand):
    help = 'Delete all product categories'

    def handle(self, *args, **options):
        # Count the number of categories before deletion
        count = StockItem.objects.count()
        
        # Delete all product categories
        StockItem.objects.all().delete()
        
        # Print a success message with the number of deleted categories
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} Variant Stocks.'))