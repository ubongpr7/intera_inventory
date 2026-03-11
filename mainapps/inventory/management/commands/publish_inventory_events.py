from django.core.management.base import BaseCommand

from mainapps.inventory.models import InventoryItem
from subapps.kafka.producers.inventory import publish_inventory_availability_upserted


class Command(BaseCommand):
    help = "Publish inventory availability projection events to Kafka."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, default=None)
        parser.add_argument("--inventory-item-id", default=None)

    def handle(self, *args, **options):
        profile_id = options["profile_id"]
        inventory_item_id = options["inventory_item_id"]

        queryset = InventoryItem.objects.all().order_by("created_at")
        if profile_id is not None:
            queryset = queryset.filter(profile_id=profile_id)
        if inventory_item_id:
            queryset = queryset.filter(id=inventory_item_id)

        published_count = 0
        skipped_count = 0

        for inventory_item in queryset.iterator():
            envelope = publish_inventory_availability_upserted(inventory_item_id=inventory_item.id)
            if envelope is None:
                skipped_count += 1
                continue
            published_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Published {published_count} inventory availability events; skipped {skipped_count} unmapped items."
            )
        )
