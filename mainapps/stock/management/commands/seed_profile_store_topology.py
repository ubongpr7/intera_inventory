from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from mainapps.inventory.models import InventoryItem
from mainapps.stock.models import StockBalance, StockLocation, StockLocationType
from subapps.services.location_scope import ensure_inventory_item_placement


STORE_SPECS = (
    {
        "name": "Gberigbe Store",
        "physical_address": "Gberigbe, Ikorodu, Lagos",
        "description": "Primary supermarket branch for profile inventory operations.",
    },
    {
        "name": "Airport Road, Oshodi Store",
        "physical_address": "Airport Road, Oshodi, Lagos",
        "description": "Urban store branch serving the Oshodi corridor.",
    },
    {
        "name": "Agric, Ikorodu Store",
        "physical_address": "Agric Bus Stop, Ikorodu, Lagos",
        "description": "Neighbourhood store branch serving the Agric corridor in Ikorodu.",
    },
    {
        "name": "Lekki Phase 1 Store",
        "physical_address": "Admiralty Way, Lekki Phase 1, Lagos",
        "description": "Premium neighbourhood branch serving Lekki Phase 1 shoppers.",
    },
    {
        "name": "Yaba Market Store",
        "physical_address": "Herbert Macaulay Way, Yaba, Lagos",
        "description": "Mid-city branch supporting Yaba foot traffic and student demand.",
    },
    {
        "name": "Surulere Central Store",
        "physical_address": "Adeniran Ogunsanya, Surulere, Lagos",
        "description": "High-throughput Surulere branch with broad convenience assortment coverage.",
    },
)

STORE_LAYOUT = (
    {"name": "Sales Floor", "type": "Showroom", "parent": None},
    {"name": "Backroom", "type": "Backroom", "parent": None},
    {"name": "Receiving Bay", "type": "Loading Dock", "parent": None},
    {"name": "Cold Room", "type": "Cold Storage", "parent": None},
    {"name": "Checkout Zone", "type": "Checkout", "parent": None},
    {"name": "Returns Desk", "type": "Returns Area", "parent": None},
    {"name": "Beauty & Fragrance Gondola", "type": "Gondola", "parent": "Sales Floor"},
    {"name": "Beverage Aisle", "type": "Aisle", "parent": "Sales Floor"},
    {"name": "Electronics Shelf A", "type": "Shelf", "parent": "Sales Floor"},
    {"name": "Fashion Rack A", "type": "Rack", "parent": "Sales Floor"},
    {"name": "Footwear Rack A", "type": "Rack", "parent": "Sales Floor"},
)

LOCATION_TYPE_DESCRIPTIONS = {
    "Warehouse": "Large storage facility",
    "Showroom": "Customer-facing display area",
    "Backroom": "Staff-only storage",
    "Loading Dock": "Goods transfer area",
    "Cold Storage": "Refrigerated storage area",
    "Checkout": "Point-of-sale storage",
    "Returns Area": "Goods return processing",
    "Gondola": "Retail freestanding display",
    "Aisle": "Passage between merchandise bays",
    "Shelf": "Horizontal storage or display surface",
    "Rack": "Store fixture for hanging or standing goods",
}

ROOT_DISTRIBUTION_RATIOS = (
    Decimal("0.55"),
    Decimal("0.25"),
    Decimal("0.20"),
)


@dataclass
class SeedSummary:
    created_locations: int = 0
    updated_locations: int = 0
    created_placements: int = 0
    redistributed_root_balances: int = 0
    created_balances: int = 0
    updated_balances: int = 0
    deleted_root_balances: int = 0


class Command(BaseCommand):
    help = "Seed a repeatable supermarket-style structural location tree for one workspace and redistribute any stock sitting on structural roots."

    def add_arguments(self, parser):
        parser.add_argument("--profile-id", type=int, required=True, help="Workspace profile id to seed.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist changes. Without this flag the command only prints the plan.",
        )

    def handle(self, *args, **options):
        profile_id = options["profile_id"]
        apply_changes = bool(options["apply"])

        if profile_id <= 0:
            raise CommandError("profile-id must be a positive integer.")

        if not InventoryItem.objects.filter(profile_id=profile_id).exists():
            self.stdout.write(self.style.WARNING(f"Profile {profile_id} has no inventory items yet. The store topology will still be seeded."))

        summary = SeedSummary()

        if apply_changes:
            with transaction.atomic():
                self._run(profile_id=profile_id, summary=summary, apply_changes=True)
        else:
            self._run(profile_id=profile_id, summary=summary, apply_changes=False)

        mode = "Applied" if apply_changes else "Planned"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} store topology seed for profile {profile_id}: "
                f"{summary.created_locations} location(s) created, "
                f"{summary.updated_locations} location(s) updated, "
                f"{summary.created_placements} placement(s) created, "
                f"{summary.redistributed_root_balances} structural-root balance(s) redistributed, "
                f"{summary.created_balances} balance(s) created, "
                f"{summary.updated_balances} balance(s) updated, "
                f"{summary.deleted_root_balances} structural-root balance(s) removed."
            )
        )

    def _run(self, *, profile_id: int, summary: SeedSummary, apply_changes: bool) -> None:
        location_types = self._ensure_location_types(apply_changes=apply_changes)
        roots, child_locations = self._ensure_store_topology(
            profile_id=profile_id,
            location_types=location_types,
            summary=summary,
            apply_changes=apply_changes,
        )
        self._ensure_workspace_placements(
            profile_id=profile_id,
            roots=roots,
            summary=summary,
            apply_changes=apply_changes,
        )
        self._redistribute_structural_root_balances(
            profile_id=profile_id,
            roots=roots,
            child_locations=child_locations,
            summary=summary,
            apply_changes=apply_changes,
        )

    def _ensure_location_types(self, *, apply_changes: bool) -> dict[str, StockLocationType]:
        resolved: dict[str, StockLocationType] = {}
        for name, description in LOCATION_TYPE_DESCRIPTIONS.items():
            location_type = StockLocationType.objects.filter(name=name).first()
            if location_type is None:
                if not apply_changes:
                    location_type = StockLocationType(name=name, description=description)
                    self.stdout.write(self.style.WARNING(f"Would create stock location type: {name}"))
                else:
                    location_type, _ = StockLocationType.objects.get_or_create(
                        name=name,
                        defaults={"description": description},
                    )
            resolved[name] = location_type
        return resolved

    def _ensure_store_topology(
        self,
        *,
        profile_id: int,
        location_types: dict[str, StockLocationType],
        summary: SeedSummary,
        apply_changes: bool,
    ) -> tuple[list[StockLocation], dict[str, dict[str, StockLocation]]]:
        roots: list[StockLocation] = []
        child_locations: dict[str, dict[str, StockLocation]] = {}

        for store in STORE_SPECS:
            root, created, updated = self._ensure_location(
                profile_id=profile_id,
                name=store["name"],
                structural=True,
                parent=None,
                location_type=location_types["Warehouse"],
                physical_address=store["physical_address"],
                description=store["description"],
                apply_changes=apply_changes,
            )
            if created:
                summary.created_locations += 1
            if updated:
                summary.updated_locations += 1
            roots.append(root)
            child_locations[root.name] = {}

            layout_map: dict[str, StockLocation] = {}
            for node in STORE_LAYOUT:
                parent = layout_map.get(node["parent"]) if node["parent"] else root
                location, child_created, child_updated = self._ensure_location(
                    profile_id=profile_id,
                    name=node["name"],
                    structural=False,
                    parent=parent,
                    location_type=location_types[node["type"]],
                    physical_address=root.physical_address,
                    description=f"{node['name']} in {root.name}",
                    apply_changes=apply_changes,
                )
                if child_created:
                    summary.created_locations += 1
                if child_updated:
                    summary.updated_locations += 1
                layout_map[node["name"]] = location
                child_locations[root.name][node["name"]] = location

        return roots, child_locations

    def _ensure_location(
        self,
        *,
        profile_id: int,
        name: str,
        structural: bool,
        parent: StockLocation | None,
        location_type: StockLocationType,
        physical_address: str,
        description: str,
        apply_changes: bool,
    ) -> tuple[StockLocation, bool, bool]:
        existing = (
            StockLocation.objects.filter(
                profile_id=profile_id,
                name=name,
                parent=parent,
            )
            .select_related("location_type", "parent")
            .first()
        )
        if existing is None:
            location = StockLocation(
                profile_id=profile_id,
                name=name,
                structural=structural,
                parent=parent,
                location_type=location_type,
                physical_address=physical_address,
                description=description,
            )
            if apply_changes:
                location.save()
                self.stdout.write(self.style.SUCCESS(f"Created location: {name}"))
            else:
                self.stdout.write(self.style.WARNING(f"Would create location: {name}"))
            return location, True, False

        changed = False
        if existing.structural != structural:
            existing.structural = structural
            changed = True
        if existing.location_type_id != location_type.id:
            existing.location_type = location_type
            changed = True
        if (existing.parent_id or None) != (parent.id if parent else None):
            existing.parent = parent
            changed = True
        if (existing.physical_address or "") != physical_address:
            existing.physical_address = physical_address
            changed = True
        if (existing.description or "") != description:
            existing.description = description
            changed = True

        if changed and apply_changes:
            existing.save()
            self.stdout.write(self.style.SUCCESS(f"Updated location: {name}"))
        elif changed:
            self.stdout.write(self.style.WARNING(f"Would update location: {name}"))
        return existing, False, changed

    def _ensure_workspace_placements(
        self,
        *,
        profile_id: int,
        roots: Iterable[StockLocation],
        summary: SeedSummary,
        apply_changes: bool,
    ) -> None:
        items = InventoryItem.objects.filter(profile_id=profile_id).order_by("name_snapshot", "id")
        for item in items:
            for root in roots:
                if not apply_changes:
                    exists = item.placements.filter(structural_location=root).exists()
                    if not exists:
                        summary.created_placements += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"Would create placement: {item.name_snapshot} -> {root.name}"
                            )
                        )
                    continue

                result = ensure_inventory_item_placement(item, stock_location=root)
                if result.created:
                    summary.created_placements += 1

    def _redistribute_structural_root_balances(
        self,
        *,
        profile_id: int,
        roots: list[StockLocation],
        child_locations: dict[str, dict[str, StockLocation]],
        summary: SeedSummary,
        apply_changes: bool,
    ) -> None:
        root_balances = (
            StockBalance.objects.select_related("inventory_item", "stock_location", "stock_lot")
            .filter(profile_id=profile_id, stock_location__structural=True)
            .order_by("created_at", "id")
        )

        for balance in root_balances:
            if balance.quantity_on_hand == 0 and balance.quantity_reserved == 0:
                continue

            target_leaf_name = self._select_leaf_name(balance.inventory_item.name_snapshot)
            target_locations = [child_locations[root.name][target_leaf_name] for root in roots]
            on_hand_shares = self._split_quantity(balance.quantity_on_hand, len(target_locations))
            reserved_shares = self._split_quantity(balance.quantity_reserved, len(target_locations))

            summary.redistributed_root_balances += 1
            if not apply_changes:
                self.stdout.write(
                    self.style.WARNING(
                        f"Would redistribute {balance.inventory_item.name_snapshot} from structural root "
                        f"{balance.stock_location.name} into {', '.join(location.name + ' @ ' + location.parent.name for location in target_locations)}"
                    )
                )
                summary.created_balances += sum(1 for share in on_hand_shares if share != 0)
                summary.deleted_root_balances += 1
                continue

            for root in roots:
                ensure_inventory_item_placement(balance.inventory_item, stock_location=root)

            for target_location, share_on_hand, share_reserved in zip(target_locations, on_hand_shares, reserved_shares):
                if share_on_hand == 0 and share_reserved == 0:
                    continue
                child_balance, created = StockBalance.objects.get_or_create(
                    inventory_item=balance.inventory_item,
                    stock_location=target_location,
                    stock_lot=balance.stock_lot,
                    defaults={
                        "profile_id": profile_id,
                        "quantity_on_hand": share_on_hand,
                        "quantity_reserved": share_reserved,
                    },
                )
                if created:
                    summary.created_balances += 1
                else:
                    child_balance.quantity_on_hand = share_on_hand
                    child_balance.quantity_reserved = share_reserved
                    child_balance.save(update_fields=["quantity_on_hand", "quantity_reserved", "quantity_available", "updated_at"])
                    summary.updated_balances += 1

            balance.delete()
            summary.deleted_root_balances += 1

    def _select_leaf_name(self, item_name: str) -> str:
        name = (item_name or "").strip().lower()
        if any(token in name for token in ("perfume", "parfum", "fragrance", "after-shave", "eau de")):
            return "Beauty & Fragrance Gondola"
        if any(token in name for token in ("wine", "drink", "beverage")):
            return "Beverage Aisle"
        if any(token in name for token in ("headphone", "sunglass", "watch", "electronic")):
            return "Electronics Shelf A"
        if any(token in name for token in ("sandal", "flip-flop", "shoe", "slipper")):
            return "Footwear Rack A"
        if any(token in name for token in ("shirt", "short", "jean", "pant", "cap", "polo", "dress")):
            return "Fashion Rack A"
        return "Backroom"

    def _split_quantity(self, quantity: Decimal, parts: int) -> list[Decimal]:
        if parts <= 0:
            return []
        if quantity == 0:
            return [Decimal("0") for _ in range(parts)]

        quantizer = Decimal("0.00001")
        ratios = ROOT_DISTRIBUTION_RATIOS[:parts]
        if len(ratios) < parts:
            equal_share = Decimal("1") / Decimal(parts)
            ratios = tuple(equal_share for _ in range(parts))

        shares: list[Decimal] = []
        running_total = Decimal("0")
        for ratio in ratios[:-1]:
            share = (quantity * ratio).quantize(quantizer, rounding=ROUND_HALF_UP)
            shares.append(share)
            running_total += share
        shares.append((quantity - running_total).quantize(quantizer, rounding=ROUND_HALF_UP))
        return shares
