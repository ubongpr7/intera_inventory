from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mainapps.inventory.models import InventoryItem
from subapps.services.location_scope import ensure_inventory_item_placement, resolve_inventory_item_structural_location


class Command(BaseCommand):
    help = "Repair or create structural inventory placements for a workspace."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, required=True, help="Workspace profile_id to repair.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist changes. Without this flag the command runs as a dry-run.",
        )

    def handle(self, *args, **options):
        profile_id = int(options["profile_id"])
        apply_changes = bool(options.get("apply"))

        items = list(
            InventoryItem.objects.select_related("inventory_category__default_location")
            .prefetch_related("placements")
            .filter(profile_id=profile_id)
            .order_by("created_at", "id")
        )
        if not items:
            raise CommandError(f"No inventory items found for profile_id={profile_id}.")

        planned_repairs: list[tuple[InventoryItem, str]] = []
        unresolved: list[InventoryItem] = []

        for item in items:
            active_location_ids = set(item.placements.filter(active=True).values_list("structural_location_id", flat=True))
            structural_location = resolve_inventory_item_structural_location(item)
            if structural_location is None:
                unresolved.append(item)
                continue
            if not active_location_ids:
                planned_repairs.append((item, f"create->{structural_location.name}"))
                continue
            if structural_location.id not in active_location_ids:
                planned_repairs.append((item, f"reactivate->{structural_location.name}"))

        if not planned_repairs and not unresolved:
            self.stdout.write(self.style.SUCCESS(f"No inventory placement repairs needed for profile_id={profile_id}."))
            return

        self.stdout.write(
            self.style.WARNING(
                f"{'Applying' if apply_changes else 'Dry-run for'} {len(planned_repairs)} inventory placement repair(s) on profile_id={profile_id}."
            )
        )
        for item, action in planned_repairs:
            self.stdout.write(f"- {item.name_snapshot}: {action}")

        if unresolved:
            self.stdout.write(self.style.WARNING(f"{len(unresolved)} inventory item(s) still have no resolvable structural location:"))
            for item in unresolved:
                self.stdout.write(f"- {item.name_snapshot} ({item.id})")

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry-run only. Re-run with --apply to persist these changes."))
            return

        with transaction.atomic():
            for item, _action in planned_repairs:
                ensure_inventory_item_placement(item)

        self.stdout.write(self.style.SUCCESS(f"Applied {len(planned_repairs)} inventory placement repair(s)."))
