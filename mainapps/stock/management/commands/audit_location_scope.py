from __future__ import annotations

import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from mainapps.inventory.models import InventoryItem, InventoryPlacement
from mainapps.stock.models import StockBalance, StockLocation
from subapps.services.inventory_read_model import get_profile_stock_analytics
from subapps.services.location_scope import (
    get_workspace_default_structural_location,
    resolve_structural_location,
)


def _to_string_decimal(value) -> str:
    return str(Decimal(str(value or 0)))


class Command(BaseCommand):
    help = "Audit structural location scope readiness for a workspace."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, required=True, help="Workspace profile_id to audit.")
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print JSON output.",
        )
        parser.add_argument(
            "--fail-on-issues",
            action="store_true",
            help="Exit with CommandError when the audit detects unresolved scope issues.",
        )

    def handle(self, *args, **options):
        profile_id = int(options["profile_id"])
        pretty = bool(options.get("pretty"))
        fail_on_issues = bool(options.get("fail_on_issues"))

        all_locations = StockLocation.objects.filter(profile_id=profile_id).order_by("created_at", "id")
        if not all_locations.exists():
            raise CommandError(f"No stock locations found for profile_id={profile_id}.")

        structural_locations = list(all_locations.filter(structural=True))
        default_structural_location = get_workspace_default_structural_location(profile_id=profile_id)
        inventory_items = list(InventoryItem.objects.filter(profile_id=profile_id).order_by("created_at", "id"))
        placements = InventoryPlacement.objects.filter(profile_id=profile_id, active=True)
        stock_balances = list(
            StockBalance.objects.filter(profile_id=profile_id)
            .select_related("stock_location", "inventory_item")
            .order_by("created_at", "id")
        )

        inventory_item_ids_with_active_placement = set(placements.values_list("inventory_item_id", flat=True))
        items_without_active_placement = [
            {
                "inventory_item_id": str(item.id),
                "name": item.name_snapshot,
            }
            for item in inventory_items
            if item.id not in inventory_item_ids_with_active_placement
        ]

        unresolved_balances: list[dict] = []
        structural_root_balances: list[dict] = []
        for balance in stock_balances:
            resolved_structural_location = resolve_structural_location(
                profile_id=profile_id,
                stock_location=balance.stock_location,
            )
            if resolved_structural_location is None:
                unresolved_balances.append(
                    {
                        "balance_id": str(balance.id),
                        "inventory_item_id": str(balance.inventory_item_id),
                        "inventory_name": balance.inventory_item.name_snapshot,
                        "stock_location_id": str(balance.stock_location_id),
                        "stock_location_name": str(balance.stock_location.name or ""),
                    }
                )
            elif balance.stock_location and balance.stock_location.structural:
                structural_root_balances.append(
                    {
                        "balance_id": str(balance.id),
                        "inventory_item_id": str(balance.inventory_item_id),
                        "inventory_name": balance.inventory_item.name_snapshot,
                        "stock_location_id": str(balance.stock_location_id),
                        "stock_location_name": str(balance.stock_location.name or ""),
                        "quantity_on_hand": _to_string_decimal(balance.quantity_on_hand),
                    }
                )

        scoped_analytics = get_profile_stock_analytics(profile_id=profile_id)
        payload = {
            "profile_id": profile_id,
            "default_structural_location_id": str(default_structural_location.id) if default_structural_location else None,
            "default_structural_location_name": str(default_structural_location.name or "") if default_structural_location else "",
            "structural_location_count": len(structural_locations),
            "total_location_count": all_locations.count(),
            "inventory_item_count": len(inventory_items),
            "active_inventory_placement_count": placements.count(),
            "inventory_items_without_active_placement": items_without_active_placement,
            "balances_without_resolved_structural_scope": unresolved_balances,
            "balances_posted_on_structural_roots": structural_root_balances,
            "stock_analytics": {
                "total_inventory_items": scoped_analytics["total_inventory_items"],
                "total_locations": scoped_analytics["total_locations"],
                "total_stock_value": _to_string_decimal(scoped_analytics["total_stock_value"]),
                "location_distribution": [
                    {
                        **row,
                        "structural_location_id": str(row["structural_location_id"]),
                        "total_quantity": _to_string_decimal(row["total_quantity"]),
                        "total_value": _to_string_decimal(row["total_value"]),
                    }
                    for row in scoped_analytics["location_distribution"]
                ],
            },
            "issues": {
                "missing_default_structural_location": default_structural_location is None,
                "inventory_items_without_active_placement_count": len(items_without_active_placement),
                "balances_without_resolved_structural_scope_count": len(unresolved_balances),
                "balances_posted_on_structural_roots_count": len(structural_root_balances),
            },
        }

        rendered = json.dumps(payload, indent=2 if pretty else None, sort_keys=pretty)
        self.stdout.write(rendered)

        if fail_on_issues and (
            payload["issues"]["missing_default_structural_location"]
            or payload["issues"]["inventory_items_without_active_placement_count"]
            or payload["issues"]["balances_without_resolved_structural_scope_count"]
        ):
            raise CommandError("Location scope audit failed. Resolve reported issues before proceeding.")
