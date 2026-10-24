from django.core.management.base import BaseCommand

from mainapps.inventory.models import InventoryItem
from subapps.services.inventory_uom import ensure_base_inventory_unit


class Command(BaseCommand):
    help = "Repair inventory items that do not have a concrete base UOM."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int)

    def handle(self, *args, **options):
        queryset = InventoryItem.objects.all().order_by("profile_id", "id")
        if options.get("profile_id") is not None:
            queryset = queryset.filter(profile_id=options["profile_id"])

        repaired = 0
        units_created = 0
        for item in queryset.iterator():
            had_unit = item.units.exists()
            ensure_base_inventory_unit(item)
            repaired += 1
            units_created += int(not had_unit)

        self.stdout.write(
            self.style.SUCCESS(
                f"Checked {repaired} inventory items; created {units_created} missing base UOMs."
            )
        )
