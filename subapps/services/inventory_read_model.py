from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Max
from django.utils import timezone

from mainapps.inventory.models import InventoryItem
from mainapps.stock.models import StockBalance, StockMovement, StockSerial
from subapps.services.location_scope import (
    get_location_scope_ids,
    resolve_structural_location,
    resolve_structural_locations,
)


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _derive_inventory_item_status(*, inventory_item: InventoryItem, current_stock_level: Decimal):
    if inventory_item.status == "archived":
        return "ARCHIVED"
    if inventory_item.status == "discontinued":
        return "DISCONTINUED"
    if inventory_item.status == "draft":
        return "DRAFT"
    if current_stock_level <= 0:
        return "OUT_OF_STOCK"
    if current_stock_level <= _to_decimal(inventory_item.minimum_stock_level):
        return "LOW_STOCK"
    if current_stock_level <= _to_decimal(inventory_item.reorder_point):
        return "REORDER_NEEDED"
    return "IN_STOCK"


def _empty_inventory_item_summary(inventory_item: InventoryItem):
    return {
        "inventory_item_id": inventory_item.id,
        "name": inventory_item.name_snapshot,
        "inventory_name": inventory_item.name_snapshot,
        "sku": inventory_item.sku_snapshot or "",
        "product_variant": inventory_item.barcode_snapshot or (
            str(inventory_item.product_variant_id) if inventory_item.product_variant_id else ""
        ),
        "quantity": Decimal("0"),
        "quantity_reserved": Decimal("0"),
        "quantity_available": Decimal("0"),
        "total_stock_value": Decimal("0"),
        "avg_purchase_price": Decimal("0"),
        "purchase_price": Decimal("0"),
        "status": _derive_inventory_item_status(
            inventory_item=inventory_item,
            current_stock_level=Decimal("0"),
        ),
        "expiry_date": None,
        "days_to_expiry": None,
        "location_id": None,
        "location_name": "",
        "structural_location_id": None,
        "structural_location_name": "",
        "location_count": 0,
        "location_breakdown": [],
        "serial_count": 0,
        "lot_count": 0,
        "last_movement_at": None,
        "has_balances": False,
        "_location_quantities": {},
        "_location_ids": set(),
        "_unit_costs": [],
    }


def _finalize_inventory_item_summary(inventory_item: InventoryItem, summary: dict):
    summary["location_count"] = len(summary.pop("_location_ids"))
    location_quantities = summary.pop("_location_quantities")
    ordered_locations = sorted(
        location_quantities.values(),
        key=lambda item: item["quantity"],
        reverse=True,
    )
    summary["location_breakdown"] = []
    for entry in ordered_locations:
        leaf_locations = sorted(
            entry.pop("_leaf_locations").values(),
            key=lambda leaf: leaf["quantity"],
            reverse=True,
        )
        summary["location_breakdown"].append(
            {
                "structural_location_id": entry["structural_location_id"],
                "structural_location_name": entry["structural_location_name"],
                "location_id": entry["structural_location_id"],
                "location_name": entry["structural_location_name"],
                "quantity": entry["quantity"],
                "total_quantity": entry["quantity"],
                "quantity_reserved": entry["quantity_reserved"],
                "quantity_available": entry["quantity_available"],
                "total_value": entry["total_value"],
                "leaf_location_count": len(leaf_locations),
                "leaf_locations": leaf_locations,
            }
        )
    if ordered_locations:
        summary["location_id"] = ordered_locations[0]["structural_location_id"]
        summary["location_name"] = ordered_locations[0]["structural_location_name"]
        summary["structural_location_id"] = ordered_locations[0]["structural_location_id"]
        summary["structural_location_name"] = ordered_locations[0]["structural_location_name"]
    unit_costs = summary.pop("_unit_costs")
    if unit_costs:
        average_cost = sum(unit_costs, Decimal("0")) / Decimal(len(unit_costs))
        summary["avg_purchase_price"] = average_cost
        summary["purchase_price"] = average_cost
    summary["status"] = _derive_inventory_item_status(
        inventory_item=inventory_item,
        current_stock_level=summary["quantity"],
    )
    if summary["expiry_date"]:
        summary["days_to_expiry"] = (summary["expiry_date"] - timezone.now().date()).days
    return summary


def _resolve_balance_location_entry(balance, *, structural_location_cache: dict):
    structural_location = None
    if balance.stock_location_id:
        structural_location = structural_location_cache.get(balance.stock_location_id)
        if structural_location is None:
            structural_location = resolve_structural_location(
                profile_id=balance.profile_id,
                stock_location=balance.stock_location,
            )
            structural_location_cache[balance.stock_location_id] = structural_location

    if structural_location is not None:
        return {
            "structural_location_id": structural_location.id,
            "structural_location_name": getattr(structural_location, "name", "Unknown Structural Location"),
            "leaf_location_id": balance.stock_location_id,
            "leaf_location_name": getattr(balance.stock_location, "name", "Unknown Location"),
        }

    location_name = getattr(balance.stock_location, "name", "Unknown Location")
    return {
        "structural_location_id": balance.stock_location_id,
        "structural_location_name": location_name,
        "leaf_location_id": balance.stock_location_id,
        "leaf_location_name": location_name,
    }


def _resolve_structural_scope_ids(*, profile_id, stock_location=None, stock_locations=None):
    resolved_locations = resolve_structural_locations(
        profile_id=profile_id,
        stock_locations=[location for location in [stock_location] if location is not None] + list(stock_locations or []),
    )
    if not resolved_locations:
        return None

    scope_ids: set = set()
    for location in resolved_locations:
        scope_ids.update(
            get_location_scope_ids(
                profile_id=profile_id,
                stock_location=location,
            )
            or [location.id]
        )
    return list(scope_ids)


def get_inventory_item_summary_map(inventory_items, *, stock_location=None, stock_locations=None, expiring_days: int = 30):
    inventory_item_list = list(inventory_items)
    if not inventory_item_list:
        return {}

    item_ids = [inventory_item.id for inventory_item in inventory_item_list]
    summaries = {
        inventory_item.id: _empty_inventory_item_summary(inventory_item)
        for inventory_item in inventory_item_list
    }

    today = timezone.now().date()
    cutoff_date = today + timedelta(days=expiring_days)
    balances = (
        StockBalance.objects.filter(inventory_item_id__in=item_ids)
        .select_related("stock_location", "stock_lot")
        .order_by("created_at")
    )
    profile_id = next(
        (
            inventory_item.profile_id
            for inventory_item in inventory_item_list
            if getattr(inventory_item, "profile_id", None) is not None
        ),
        None,
    )
    scoped_location_ids = (
        _resolve_structural_scope_ids(
            profile_id=profile_id,
            stock_location=stock_location,
            stock_locations=stock_locations,
        )
        if profile_id is not None and (stock_location is not None or stock_locations)
        else None
    )
    if scoped_location_ids is not None:
        balances = balances.filter(stock_location_id__in=scoped_location_ids)

    structural_location_cache: dict = {}

    for balance in balances:
        summary = summaries.get(balance.inventory_item_id)
        if summary is None:
            continue

        quantity_on_hand = _to_decimal(balance.quantity_on_hand)
        quantity_reserved = _to_decimal(balance.quantity_reserved)
        quantity_available = _to_decimal(balance.quantity_available)
        aggregate = None
        leaf_aggregate = None

        summary["has_balances"] = True
        summary["quantity"] += quantity_on_hand
        summary["quantity_reserved"] += quantity_reserved
        summary["quantity_available"] += quantity_available

        if balance.stock_location_id:
            location_entry = _resolve_balance_location_entry(
                balance,
                structural_location_cache=structural_location_cache,
            )
            structural_location_id = location_entry["structural_location_id"]
            leaf_location_id = location_entry["leaf_location_id"]
            summary["_location_ids"].add(structural_location_id)
            aggregate = summary["_location_quantities"].setdefault(
                structural_location_id,
                {
                    "structural_location_id": structural_location_id,
                    "structural_location_name": location_entry["structural_location_name"],
                    "quantity": Decimal("0"),
                    "quantity_reserved": Decimal("0"),
                    "quantity_available": Decimal("0"),
                    "total_value": Decimal("0"),
                    "_leaf_locations": {},
                },
            )
            aggregate["quantity"] += quantity_on_hand
            aggregate["quantity_reserved"] += quantity_reserved
            aggregate["quantity_available"] += quantity_available
            leaf_aggregate = aggregate["_leaf_locations"].setdefault(
                leaf_location_id,
                {
                    "stock_location_id": leaf_location_id,
                    "stock_location_name": location_entry["leaf_location_name"],
                    "quantity": Decimal("0"),
                    "total_quantity": Decimal("0"),
                    "quantity_reserved": Decimal("0"),
                    "quantity_available": Decimal("0"),
                    "total_value": Decimal("0"),
                },
            )
            leaf_aggregate["quantity"] += quantity_on_hand
            leaf_aggregate["total_quantity"] += quantity_on_hand
            leaf_aggregate["quantity_reserved"] += quantity_reserved
            leaf_aggregate["quantity_available"] += quantity_available
            if summary["location_id"] is None and quantity_on_hand > 0:
                summary["location_id"] = structural_location_id
                summary["location_name"] = location_entry["structural_location_name"]
                summary["structural_location_id"] = structural_location_id
                summary["structural_location_name"] = location_entry["structural_location_name"]

        if balance.stock_lot_id:
            unit_cost = _to_decimal(balance.stock_lot.unit_cost)
            summary["total_stock_value"] += quantity_on_hand * unit_cost
            if aggregate is not None and leaf_aggregate is not None:
                aggregate["total_value"] += quantity_on_hand * unit_cost
                leaf_aggregate["total_value"] += quantity_on_hand * unit_cost
            if quantity_on_hand > 0:
                summary["_unit_costs"].append(unit_cost)
            if quantity_on_hand > 0:
                summary["lot_count"] += 1
            if (
                balance.stock_lot.expiry_date
                and today <= balance.stock_lot.expiry_date <= cutoff_date
                and quantity_on_hand > 0
                and (summary["expiry_date"] is None or balance.stock_lot.expiry_date < summary["expiry_date"])
            ):
                summary["expiry_date"] = balance.stock_lot.expiry_date

    movement_map = StockMovement.objects.filter(
        inventory_item_id__in=item_ids
    ).values("inventory_item_id").annotate(last_movement_at=Max("occurred_at"))
    for row in movement_map:
        summary = summaries.get(row["inventory_item_id"])
        if summary is not None:
            summary["last_movement_at"] = row["last_movement_at"]

    serial_counts = {
        row["inventory_item_id"]: row["count"]
        for row in (
            StockSerial.objects.filter(inventory_item_id__in=item_ids)
            .values("inventory_item_id")
            .annotate(count=Count("id"))
        )
    }

    for inventory_item in inventory_item_list:
        summary = summaries[inventory_item.id]
        if not summary["serial_count"]:
            summary["serial_count"] = serial_counts.get(inventory_item.id, 0)
        _finalize_inventory_item_summary(inventory_item, summary)

    return summaries


def get_inventory_ids_for_stock_filter(inventories, *, filter_name: str, stock_location=None, stock_locations=None):
    summary_map = get_inventory_item_summary_map(
        inventories,
        stock_location=stock_location,
        stock_locations=stock_locations,
    )
    inventory_ids = []
    for inventory in inventories:
        summary = summary_map.get(inventory.id, {})
        current_stock = _to_decimal(summary.get("quantity"))
        minimum_stock_level = _to_decimal(inventory.minimum_stock_level)
        reorder_point = _to_decimal(inventory.reorder_point)
        if filter_name == "low_stock" and minimum_stock_level > 0 and 0 < current_stock <= minimum_stock_level:
            inventory_ids.append(inventory.id)
        elif filter_name == "needs_reorder" and reorder_point > 0 and current_stock <= reorder_point:
            inventory_ids.append(inventory.id)
        elif filter_name == "out_of_stock" and current_stock <= 0:
            inventory_ids.append(inventory.id)
    return inventory_ids


def get_location_stock_summary(location, *, expiring_days: int = 30):
    today = timezone.now().date()
    cutoff_date = today + timedelta(days=expiring_days)
    scope_ids = get_location_scope_ids(
        profile_id=location.profile_id,
        stock_location=location,
    )
    balances = (
        StockBalance.objects.filter(
            profile_id=location.profile_id,
            stock_location_id__in=scope_ids or [location.id],
            quantity_on_hand__gt=0,
        )
        .select_related("inventory_item", "stock_lot")
    )

    total_items = 0
    total_quantity = Decimal("0")
    total_value = Decimal("0")
    inventory_type_counts = defaultdict(int)

    for balance in balances:
        total_items += 1
        quantity_on_hand = _to_decimal(balance.quantity_on_hand)
        total_quantity += quantity_on_hand
        if balance.stock_lot_id:
            total_value += quantity_on_hand * _to_decimal(balance.stock_lot.unit_cost)
        inventory_type_counts[balance.inventory_item.inventory_type] += 1

    return {
        "total_items": total_items,
        "total_quantity": total_quantity,
        "total_value": total_value,
        "top_inventory_types": [
            {"inventory_type": inventory_type, "count": count}
            for inventory_type, count in sorted(
                inventory_type_counts.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
        ],
        "expiring_soon_count": balances.filter(
            stock_lot__expiry_date__gte=today,
            stock_lot__expiry_date__lte=cutoff_date,
        ).count(),
    }


def get_profile_stock_analytics(*, profile_id: int, stock_location=None, stock_locations=None):
    today = timezone.now().date()
    balances = StockBalance.objects.filter(profile_id=profile_id).select_related(
        "stock_location",
        "stock_lot",
        "inventory_item",
    )
    scoped_location_ids = _resolve_structural_scope_ids(
        profile_id=profile_id,
        stock_location=stock_location,
        stock_locations=stock_locations,
    ) if stock_location is not None or stock_locations else None
    if scoped_location_ids is not None:
        balances = balances.filter(stock_location_id__in=scoped_location_ids)

    total_inventory_items = set()
    total_locations = set()
    total_stock_value = Decimal("0")
    location_distribution = {}
    aging_analysis = {
        "0-30_days": 0,
        "31-90_days": 0,
        "91-365_days": 0,
        "over_1_year": 0,
    }
    structural_location_cache: dict = {}

    for balance in balances:
        quantity_on_hand = _to_decimal(balance.quantity_on_hand)
        if quantity_on_hand <= 0:
            continue

        total_inventory_items.add(balance.inventory_item_id)
        if balance.stock_location_id:
            location_entry = _resolve_balance_location_entry(
                balance,
                structural_location_cache=structural_location_cache,
            )
            structural_location_id = location_entry["structural_location_id"]
            total_locations.add(structural_location_id)
            aggregate = location_distribution.setdefault(
                structural_location_id,
                {
                    "structural_location_id": structural_location_id,
                    "location_name": location_entry["structural_location_name"],
                    "item_count": 0,
                    "total_quantity": Decimal("0"),
                    "total_value": Decimal("0"),
                },
            )
            aggregate["item_count"] += 1
            aggregate["total_quantity"] += quantity_on_hand
            if balance.stock_lot_id:
                aggregate["total_value"] += quantity_on_hand * _to_decimal(balance.stock_lot.unit_cost)

        if balance.stock_lot_id:
            total_stock_value += quantity_on_hand * _to_decimal(balance.stock_lot.unit_cost)

        reference_date = balance.stock_lot.created_at.date() if balance.stock_lot_id else balance.created_at.date()
        age_days = (today - reference_date).days
        if age_days <= 30:
            aging_analysis["0-30_days"] += 1
        elif age_days <= 90:
            aging_analysis["31-90_days"] += 1
        elif age_days <= 365:
            aging_analysis["91-365_days"] += 1
        else:
            aging_analysis["over_1_year"] += 1

    return {
        "total_inventory_items": len(total_inventory_items),
        "total_locations": len(total_locations),
        "total_stock_value": total_stock_value,
        "location_distribution": [
            {
                "structural_location_id": values["structural_location_id"],
                "location_name": values["location_name"],
                "item_count": values["item_count"],
                "total_quantity": values["total_quantity"],
                "total_value": values["total_value"],
            }
            for values in (
                item[1]
                for item in sorted(
                    location_distribution.items(),
                    key=lambda item: item[1]["total_quantity"],
                    reverse=True,
                )
            )
        ],
        "aging_analysis": aging_analysis,
    }


def get_low_stock_rows(inventories, *, stock_location=None, stock_locations=None):
    summary_map = get_inventory_item_summary_map(
        inventories,
        stock_location=stock_location,
        stock_locations=stock_locations,
    )
    rows = []
    for inventory in inventories:
        summary = summary_map.get(inventory.id, {})
        current_stock = _to_decimal(summary.get("quantity"))
        minimum_stock_level = _to_decimal(inventory.minimum_stock_level)
        if minimum_stock_level > 0 and 0 < current_stock <= minimum_stock_level:
            rows.append(
                {
                    "id": inventory.id,
                    "name": inventory.name_snapshot,
                    "sku": inventory.sku_snapshot or "",
                    "quantity": current_stock,
                    "inventory_name": inventory.name_snapshot,
                    "minimum_stock_level": minimum_stock_level,
                    "reorder_point": _to_decimal(inventory.reorder_point),
                    "shortfall": minimum_stock_level - current_stock,
                    "product_variant": inventory.barcode_snapshot or (
                        str(inventory.product_variant_id) if inventory.product_variant_id else ""
                    ),
                    "display_image": None,
                }
            )
    rows.sort(key=lambda row: row["shortfall"], reverse=True)
    return rows
