from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional
from uuid import UUID

from django.db import transaction

from mainapps.inventory.models import InventoryItem, InventoryPlacement
from mainapps.stock.models import StockLocation, ensure_single_default_structural_location


def _coerce_uuid(value) -> UUID | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _scope_locations(*, profile_id):
    return StockLocation.objects.filter(profile_id=profile_id)


def get_workspace_default_structural_location(*, profile_id) -> Optional[StockLocation]:
    queryset = _scope_locations(profile_id=profile_id)
    default_location = queryset.filter(structural=True, is_default_structural_location=True).order_by("created_at", "id").first()
    if default_location is not None:
        return default_location
    return ensure_single_default_structural_location(profile_id=profile_id)


def resolve_structural_location(*, profile_id, stock_location: StockLocation | None = None, stock_location_id=None) -> Optional[StockLocation]:
    location = stock_location
    resolved_location_id = _coerce_uuid(stock_location_id)
    if location is None and resolved_location_id is not None:
        location = _scope_locations(profile_id=profile_id).filter(id=resolved_location_id).first()
    if location is None:
        return None
    if location.structural:
        return location

    current = location
    visited_ids: set[UUID] = set()
    while current is not None and current.id not in visited_ids:
        visited_ids.add(current.id)
        parent_id = getattr(current, "parent_id", None)
        if parent_id is None:
            break
        current = _scope_locations(profile_id=profile_id).filter(id=parent_id).first()
        if current is not None and current.structural:
            return current
    return None


def resolve_structural_locations(
    *,
    profile_id,
    stock_locations: Iterable[StockLocation] | None = None,
    stock_location_ids: Iterable | None = None,
) -> list[StockLocation]:
    resolved_locations: list[StockLocation] = []
    seen_ids: set[UUID] = set()

    for location in stock_locations or []:
        structural_location = resolve_structural_location(profile_id=profile_id, stock_location=location)
        if structural_location is None or structural_location.id in seen_ids:
            continue
        seen_ids.add(structural_location.id)
        resolved_locations.append(structural_location)

    for location_id in stock_location_ids or []:
        structural_location = resolve_structural_location(profile_id=profile_id, stock_location_id=location_id)
        if structural_location is None or structural_location.id in seen_ids:
            continue
        seen_ids.add(structural_location.id)
        resolved_locations.append(structural_location)

    return resolved_locations


def get_location_scope_ids(
    *,
    profile_id,
    stock_location: StockLocation | None = None,
    stock_location_id=None,
    include_descendants_for_structural: bool = True,
) -> list[UUID]:
    location = stock_location
    resolved_location_id = _coerce_uuid(stock_location_id)
    if location is None and resolved_location_id is not None:
        location = _scope_locations(profile_id=profile_id).filter(id=resolved_location_id).first()
    if location is None:
        return []
    if include_descendants_for_structural and location.structural:
        return list(
            _scope_locations(profile_id=profile_id)
            .filter(tree_id=location.tree_id, lft__gte=location.lft, rght__lte=location.rght)
            .values_list("id", flat=True)
        )
    return [location.id]


def get_location_scope_ids_for_locations(
    *,
    profile_id,
    stock_locations: Iterable[StockLocation] | None = None,
    stock_location_ids: Iterable | None = None,
    include_descendants_for_structural: bool = True,
) -> list[UUID]:
    scoped_ids: list[UUID] = []
    seen_ids: set[UUID] = set()

    for location in stock_locations or []:
        for scoped_id in get_location_scope_ids(
            profile_id=profile_id,
            stock_location=location,
            include_descendants_for_structural=include_descendants_for_structural,
        ):
            if scoped_id in seen_ids:
                continue
            seen_ids.add(scoped_id)
            scoped_ids.append(scoped_id)

    for location_id in stock_location_ids or []:
        for scoped_id in get_location_scope_ids(
            profile_id=profile_id,
            stock_location_id=location_id,
            include_descendants_for_structural=include_descendants_for_structural,
        ):
            if scoped_id in seen_ids:
                continue
            seen_ids.add(scoped_id)
            scoped_ids.append(scoped_id)

    return scoped_ids


def resolve_inventory_item_structural_location(
    inventory_item: InventoryItem,
    *,
    stock_location: StockLocation | None = None,
    stock_location_id=None,
) -> Optional[StockLocation]:
    profile_id = inventory_item.profile_id
    resolved = resolve_structural_location(
        profile_id=profile_id,
        stock_location=stock_location,
        stock_location_id=stock_location_id,
    )
    if resolved is not None:
        return resolved

    category = inventory_item.inventory_category
    if category and category.default_location_id:
        resolved = resolve_structural_location(
            profile_id=profile_id,
            stock_location=category.default_location,
        )
        if resolved is not None:
            return resolved

    return get_workspace_default_structural_location(profile_id=profile_id)


@dataclass
class InventoryPlacementResult:
    placement: Optional[InventoryPlacement]
    created: bool
    structural_location: Optional[StockLocation]


@transaction.atomic
def ensure_inventory_item_placement(
    inventory_item: InventoryItem,
    *,
    stock_location: StockLocation | None = None,
    stock_location_id=None,
    created_by_user_id=None,
    updated_by_user_id=None,
) -> InventoryPlacementResult:
    structural_location = resolve_inventory_item_structural_location(
        inventory_item,
        stock_location=stock_location,
        stock_location_id=stock_location_id,
    )
    if structural_location is None:
        return InventoryPlacementResult(placement=None, created=False, structural_location=None)

    placement, created = InventoryPlacement.objects.get_or_create(
        inventory_item=inventory_item,
        structural_location=structural_location,
        defaults={
            "profile_id": inventory_item.profile_id,
            "created_by_user_id": created_by_user_id,
            "updated_by_user_id": updated_by_user_id,
            "location_name_snapshot": str(structural_location.name or ""),
            "active": True,
        },
    )
    if not created:
        changed_fields: list[str] = []
        snapshot = str(structural_location.name or "")
        if placement.location_name_snapshot != snapshot:
            placement.location_name_snapshot = snapshot
            changed_fields.append("location_name_snapshot")
        if not placement.active:
            placement.active = True
            changed_fields.append("active")
        if updated_by_user_id is not None and placement.updated_by_user_id != updated_by_user_id:
            placement.updated_by_user_id = updated_by_user_id
            changed_fields.append("updated_by_user_id")
        if changed_fields:
            placement.save(update_fields=changed_fields + ["updated_at"])

    return InventoryPlacementResult(
        placement=placement,
        created=created,
        structural_location=structural_location,
    )
