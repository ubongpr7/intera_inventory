from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mainapps.projections.models import CatalogVariantProjection
from subapps.services.inventory_variant_cleanup import (
    delete_inventory_item_if_safe,
    find_catalog_orphan_inventory_items,
)


class Command(BaseCommand):
    help = "Delete zero-stock catalog-sourced inventory items whose backing product variants no longer exist."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, required=True, help="Workspace profile_id to repair.")
        parser.add_argument(
            "--valid-variant-ids-file",
            default=None,
            help="Optional JSON file containing the authoritative list of current product-service variant IDs.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist deletions. Without this flag the command runs as a dry-run.",
        )

    def _load_variant_ids(self, *, profile_id: int, variant_ids_file: str | None) -> set[str]:
        if variant_ids_file:
            path = Path(variant_ids_file)
            if not path.exists():
                raise CommandError(f"Variant ID file not found: {path}")
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError as exc:
                raise CommandError(f"Variant ID file is not valid JSON: {path}") from exc
            if not isinstance(payload, list):
                raise CommandError("Variant ID file must contain a JSON array of UUID strings.")
            return {str(value).strip() for value in payload if str(value).strip()}

        return set(
            CatalogVariantProjection.objects.filter(profile_id=profile_id)
            .values_list("variant_id", flat=True)
        )

    def handle(self, *args, **options):
        profile_id = int(options["profile_id"])
        apply_changes = bool(options.get("apply"))
        valid_variant_ids = self._load_variant_ids(
            profile_id=profile_id,
            variant_ids_file=options.get("valid_variant_ids_file"),
        )

        orphan_items = list(
            find_catalog_orphan_inventory_items(profile_id=profile_id, valid_variant_ids=valid_variant_ids)
        )
        if not orphan_items:
            self.stdout.write(self.style.SUCCESS(f"No orphan inventory items found for profile_id={profile_id}."))
            return

        self.stdout.write(
            self.style.WARNING(
                f"{'Applying' if apply_changes else 'Dry-run for'} cleanup of {len(orphan_items)} orphan inventory item(s) on profile_id={profile_id}."
            )
        )

        deleted_ids: list[str] = []
        blocked_by_stock: list[str] = []
        blocked_by_relations: list[str] = []

        def _process(*, apply: bool) -> None:
            for inventory_item in orphan_items:
                if apply:
                    outcome = delete_inventory_item_if_safe(inventory_item)
                else:
                    from subapps.services.inventory_variant_cleanup import (
                        get_inventory_item_delete_blockers,
                        inventory_item_has_non_zero_stock,
                    )

                    blocked_by_stock_flag = inventory_item_has_non_zero_stock(inventory_item)
                    blockers = {} if blocked_by_stock_flag else get_inventory_item_delete_blockers(inventory_item)
                    outcome_deleted = not blocked_by_stock_flag and not blockers
                    from subapps.services.inventory_variant_cleanup import InventoryItemDeleteOutcome

                    outcome = InventoryItemDeleteOutcome(
                        inventory_item_id=str(inventory_item.id),
                        deleted=outcome_deleted,
                        blocked_by_stock=blocked_by_stock_flag,
                        blocked_relations=blockers,
                        reason="dry-run",
                    )
                if outcome.deleted:
                    deleted_ids.append(outcome.inventory_item_id)
                    self.stdout.write(self.style.SUCCESS(f"DELETE {outcome.inventory_item_id} {inventory_item.name_snapshot}"))
                    continue

                if outcome.blocked_by_stock:
                    blocked_by_stock.append(outcome.inventory_item_id)
                    self.stdout.write(
                        self.style.WARNING(
                            f"KEEP {outcome.inventory_item_id} {inventory_item.name_snapshot} - non-zero stock"
                        )
                    )
                    continue

                blocked_by_relations.append(outcome.inventory_item_id)
                blocker_summary = ", ".join(
                    f"{name}={count}" for name, count in sorted(outcome.blocked_relations.items())
                ) or "unknown blocker"
                self.stdout.write(
                    self.style.WARNING(
                        f"KEEP {outcome.inventory_item_id} {inventory_item.name_snapshot} - protected refs: {blocker_summary}"
                    )
                )

        if apply_changes:
            with transaction.atomic():
                _process(apply=True)
        else:
            _process(apply=False)
            self.stdout.write(self.style.WARNING("Dry-run only. Re-run with --apply to persist deletions."))

        self.stdout.write(
            self.style.SUCCESS(
                f"Cleanup summary profile_id={profile_id}: deleted={len(deleted_ids)} blocked_by_stock={len(blocked_by_stock)} blocked_by_relations={len(blocked_by_relations)}"
            )
        )
