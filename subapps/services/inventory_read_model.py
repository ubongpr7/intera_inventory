from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import models
from django.db.models import Avg, Count, F, Max, Sum
from django.utils import timezone

from mainapps.inventory.models import Inventory, InventoryItem
from mainapps.stock.models import StockBalance, StockItem, StockMovement, StockSerial


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _empty_inventory_summary(inventory: Inventory):
    return {
        "inventory_id": inventory.id,
        "inventory_name": inventory.name,
        "external_system_id": inventory.external_system_id or "",
        "inventory_type": inventory.inventory_type,
        "current_stock_level": Decimal("0"),
        "quantity_reserved": Decimal("0"),
        "quantity_available": Decimal("0"),
        "total_stock_value": Decimal("0"),
        "total_locations": 0,
        "avg_purchase_price": Decimal("0"),
        "stock_status": "",
        "expiring_soon_count": 0,
        "location_breakdown": [],
        "expiring_lots": [],
        "has_balances": False,
        "_location_ids": set(),
        "_location_quantities": defaultdict(Decimal),
        "_unit_costs": [],
    }


def _finalize_inventory_summary(inventory: Inventory, summary: dict):
    summary["total_locations"] = len(summary.pop("_location_ids"))
    location_quantities = summary.pop("_location_quantities")
    summary["location_breakdown"] = [
        {"location_name": location_name, "quantity": quantity}
        for location_name, quantity in sorted(
            location_quantities.items(),
            key=lambda item: item[1],
            reverse=True,
        )
    ]
    unit_costs = summary.pop("_unit_costs")
    if unit_costs:
        summary["avg_purchase_price"] = sum(unit_costs, Decimal("0")) / Decimal(len(unit_costs))
    summary["stock_status"] = _derive_stock_status(
        inventory=inventory,
        current_stock_level=summary["current_stock_level"],
    )
    return summary


def _derive_stock_status(*, inventory: Inventory, current_stock_level: Decimal):
    if current_stock_level <= 0:
        return "OUT_OF_STOCK"
    if current_stock_level <= _to_decimal(inventory.minimum_stock_level):
        return "LOW_STOCK"
    if current_stock_level <= _to_decimal(inventory.re_order_point):
        return "REORDER_NEEDED"
    return "IN_STOCK"


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


def get_inventory_summary_map(inventories, *, expiring_days: int = 30):
    inventory_list = list(inventories)
    if not inventory_list:
        return {}

    bridge_map = {
        InventoryItem.legacy_bridge_id(inventory.id): inventory
        for inventory in inventory_list
    }
    summaries = {
        inventory.id: _empty_inventory_summary(inventory)
        for inventory in inventory_list
    }

    today = timezone.now().date()
    cutoff_date = today + timedelta(days=expiring_days)
    balances = StockBalance.objects.filter(
        inventory_item_id__in=bridge_map.keys()
    ).select_related("stock_location", "stock_lot")

    for balance in balances:
        inventory = bridge_map.get(balance.inventory_item_id)
        if inventory is None:
            continue

        summary = summaries[inventory.id]
        quantity_on_hand = _to_decimal(balance.quantity_on_hand)
        quantity_reserved = _to_decimal(balance.quantity_reserved)
        quantity_available = _to_decimal(balance.quantity_available)

        summary["has_balances"] = True
        summary["current_stock_level"] += quantity_on_hand
        summary["quantity_reserved"] += quantity_reserved
        summary["quantity_available"] += quantity_available

        if quantity_on_hand > 0 and balance.stock_location_id:
            summary["_location_ids"].add(balance.stock_location_id)
            location_name = getattr(balance.stock_location, "name", "Unknown Location")
            summary["_location_quantities"][location_name] += quantity_on_hand

        if balance.stock_lot_id:
            unit_cost = _to_decimal(balance.stock_lot.unit_cost)
            summary["total_stock_value"] += quantity_on_hand * unit_cost
            if quantity_on_hand > 0:
                summary["_unit_costs"].append(unit_cost)

            if (
                balance.stock_lot.expiry_date
                and today <= balance.stock_lot.expiry_date <= cutoff_date
                and quantity_on_hand > 0
            ):
                summary["expiring_soon_count"] += 1
                summary["expiring_lots"].append(
                    {
                        "lot_number": balance.stock_lot.lot_number,
                        "expiry_date": balance.stock_lot.expiry_date,
                        "quantity": quantity_on_hand,
                        "location_name": getattr(balance.stock_location, "name", ""),
                    }
                )

    for inventory in inventory_list:
        summary = summaries[inventory.id]
        if summary["has_balances"]:
            _finalize_inventory_summary(inventory, summary)
            continue

        legacy_aggregate = inventory.stock_items.aggregate(
            total_quantity=Sum("quantity"),
            total_value=Sum(
                F("quantity") * F("purchase_price"),
                output_field=models.DecimalField(max_digits=20, decimal_places=5),
            ),
            total_locations=Count("location", distinct=True),
            avg_purchase_price=Avg("purchase_price"),
        )

        summary["current_stock_level"] = _to_decimal(legacy_aggregate["total_quantity"])
        summary["quantity_available"] = summary["current_stock_level"]
        summary["total_stock_value"] = _to_decimal(legacy_aggregate["total_value"])
        summary["total_locations"] = legacy_aggregate["total_locations"] or 0
        summary["avg_purchase_price"] = _to_decimal(legacy_aggregate["avg_purchase_price"])

        location_breakdown = inventory.stock_items.values(
            "location__name"
        ).annotate(quantity=Sum("quantity")).order_by("-quantity")
        summary["location_breakdown"] = [
            {
                "location_name": row["location__name"] or "Unknown Location",
                "quantity": _to_decimal(row["quantity"]),
            }
            for row in location_breakdown
        ]

        expiring_items = inventory.stock_items.filter(
            expiry_date__gte=today,
            expiry_date__lte=cutoff_date,
            expiry_date__isnull=False,
        ).order_by("expiry_date")
        summary["expiring_soon_count"] = expiring_items.count()
        summary["expiring_lots"] = [
            {
                "lot_number": item.batch or "",
                "expiry_date": item.expiry_date,
                "quantity": _to_decimal(item.quantity),
                "location_name": getattr(item.location, "name", ""),
            }
            for item in expiring_items[:10]
        ]
        summary["stock_status"] = _derive_stock_status(
            inventory=inventory,
            current_stock_level=summary["current_stock_level"],
        )
        summary.pop("_location_ids", None)
        summary.pop("_location_quantities", None)
        summary.pop("_unit_costs", None)

    return summaries


def _empty_inventory_item_summary(inventory_item: InventoryItem):
    return {
        "inventory_item_id": inventory_item.id,
        "inventory_id": (inventory_item.metadata or {}).get("legacy_inventory_id"),
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
        "location_count": 0,
        "location_breakdown": [],
        "serial_count": 0,
        "lot_count": 0,
        "last_movement_at": None,
        "has_balances": False,
        "_location_quantities": defaultdict(Decimal),
        "_location_ids": set(),
        "_unit_costs": [],
    }


def _finalize_inventory_item_summary(inventory_item: InventoryItem, summary: dict):
    summary["location_count"] = len(summary.pop("_location_ids"))
    location_quantities = summary.pop("_location_quantities")
    ordered_locations = sorted(
        location_quantities.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    summary["location_breakdown"] = [
        {"location_name": location_name, "quantity": quantity}
        for location_name, quantity in ordered_locations
    ]
    if ordered_locations:
        summary["location_name"] = ordered_locations[0][0]
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


def get_inventory_item_summary_map(inventory_items, *, stock_location=None, expiring_days: int = 30):
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
    if stock_location is not None:
        balances = balances.filter(stock_location=stock_location)

    for balance in balances:
        summary = summaries.get(balance.inventory_item_id)
        if summary is None:
            continue

        quantity_on_hand = _to_decimal(balance.quantity_on_hand)
        quantity_reserved = _to_decimal(balance.quantity_reserved)
        quantity_available = _to_decimal(balance.quantity_available)

        summary["has_balances"] = True
        summary["quantity"] += quantity_on_hand
        summary["quantity_reserved"] += quantity_reserved
        summary["quantity_available"] += quantity_available

        if balance.stock_location_id:
            summary["_location_ids"].add(balance.stock_location_id)
            location_name = getattr(balance.stock_location, "name", "Unknown Location")
            summary["_location_quantities"][location_name] += quantity_on_hand
            if summary["location_id"] is None and quantity_on_hand > 0:
                summary["location_id"] = balance.stock_location_id

        if balance.stock_lot_id:
            unit_cost = _to_decimal(balance.stock_lot.unit_cost)
            summary["total_stock_value"] += quantity_on_hand * unit_cost
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

    legacy_rows = (
        StockItem.objects.filter(inventory_item_id__in=item_ids)
        .select_related("location")
        .order_by("created_at")
    )
    if stock_location is not None:
        legacy_rows = legacy_rows.filter(location=stock_location)

    for stock_item in legacy_rows:
        summary = summaries.get(stock_item.inventory_item_id)
        if summary is None:
            continue

        summary["serial_count"] = max(summary["serial_count"], serial_counts.get(stock_item.inventory_item_id, 0))
        if summary["has_balances"]:
            continue

        quantity = _to_decimal(stock_item.quantity)
        summary["quantity"] += quantity
        summary["quantity_available"] += quantity
        summary["purchase_price"] = _to_decimal(stock_item.purchase_price)
        if quantity > 0:
            summary["total_stock_value"] += quantity * _to_decimal(stock_item.purchase_price)
        if stock_item.location_id:
            location_name = getattr(stock_item.location, "name", "Unknown Location")
            summary["_location_ids"].add(stock_item.location_id)
            summary["_location_quantities"][location_name] += quantity
            if summary["location_id"] is None and quantity > 0:
                summary["location_id"] = stock_item.location_id
        if stock_item.batch:
            summary["lot_count"] += 1
        if stock_item.serial:
            summary["serial_count"] = max(summary["serial_count"], 1)
        if (
            stock_item.expiry_date
            and today <= stock_item.expiry_date <= cutoff_date
            and quantity > 0
            and (summary["expiry_date"] is None or stock_item.expiry_date < summary["expiry_date"])
        ):
            summary["expiry_date"] = stock_item.expiry_date

    for inventory_item in inventory_item_list:
        summary = summaries[inventory_item.id]
        if not summary["serial_count"]:
            summary["serial_count"] = serial_counts.get(inventory_item.id, 0)
        _finalize_inventory_item_summary(inventory_item, summary)

    return summaries


def get_inventory_ids_for_stock_filter(inventories, *, filter_name: str):
    summary_map = get_inventory_summary_map(inventories)
    inventory_ids = []
    for inventory in inventories:
        summary = summary_map.get(inventory.id, {})
        current_stock = _to_decimal(summary.get("current_stock_level"))
        if filter_name == "low_stock" and current_stock <= _to_decimal(inventory.minimum_stock_level):
            inventory_ids.append(inventory.id)
        elif filter_name == "needs_reorder" and current_stock <= _to_decimal(inventory.re_order_point):
            inventory_ids.append(inventory.id)
        elif filter_name == "out_of_stock" and current_stock <= 0:
            inventory_ids.append(inventory.id)
    return inventory_ids


def get_location_stock_summary(location, *, expiring_days: int = 30):
    today = timezone.now().date()
    cutoff_date = today + timedelta(days=expiring_days)
    balances = location.stock_balances.select_related(
        "inventory_item",
        "stock_lot",
    ).filter(quantity_on_hand__gt=0)

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

    if total_items == 0:
        legacy_items = location.stock_items.all()
        legacy_aggregate = legacy_items.aggregate(
            total_items=Count("id"),
            total_quantity=Sum("quantity"),
            total_value=Sum(
                F("quantity") * F("purchase_price"),
                output_field=models.DecimalField(max_digits=20, decimal_places=5),
            ),
        )
        total_items = legacy_aggregate["total_items"] or 0
        total_quantity = _to_decimal(legacy_aggregate["total_quantity"])
        total_value = _to_decimal(legacy_aggregate["total_value"])
        inventory_type_counts = defaultdict(int)
        for row in legacy_items.values("inventory__inventory_type").annotate(count=Count("id")):
            inventory_type_counts[row["inventory__inventory_type"] or "unknown"] += row["count"]

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


def get_profile_stock_analytics(*, profile_id: int):
    today = timezone.now().date()
    balances = StockBalance.objects.filter(profile_id=profile_id).select_related(
        "stock_location",
        "stock_lot",
        "inventory_item",
    )

    total_stock_items = set()
    total_locations = set()
    total_stock_value = Decimal("0")
    location_distribution = defaultdict(lambda: {"item_count": 0, "total_quantity": Decimal("0"), "total_value": Decimal("0")})
    aging_analysis = {
        "0-30_days": 0,
        "31-90_days": 0,
        "91-365_days": 0,
        "over_1_year": 0,
    }

    for balance in balances:
        quantity_on_hand = _to_decimal(balance.quantity_on_hand)
        if quantity_on_hand <= 0:
            continue

        total_stock_items.add(balance.inventory_item_id)
        if balance.stock_location_id:
            total_locations.add(balance.stock_location_id)
            location_name = getattr(balance.stock_location, "name", "Unknown Location")
            location_distribution[location_name]["item_count"] += 1
            location_distribution[location_name]["total_quantity"] += quantity_on_hand
            if balance.stock_lot_id:
                location_distribution[location_name]["total_value"] += quantity_on_hand * _to_decimal(balance.stock_lot.unit_cost)

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

    if not total_stock_items:
        legacy_items = StockItem.objects.filter(
            models.Q(inventory__profile_id=profile_id) | models.Q(inventory__profile=str(profile_id))
        ).select_related('location')
        legacy_aggregate = legacy_items.aggregate(
            total_items=Count('id'),
            total_locations=Count('location', distinct=True),
            total_stock_value=Sum(
                F('quantity') * F('purchase_price'),
                output_field=models.DecimalField(max_digits=20, decimal_places=5),
            ),
        )
        location_distribution = defaultdict(lambda: {"item_count": 0, "total_quantity": Decimal("0"), "total_value": Decimal("0")})
        for item in legacy_items:
            location_name = getattr(item.location, 'name', 'Unknown Location')
            location_distribution[location_name]["item_count"] += 1
            location_distribution[location_name]["total_quantity"] += _to_decimal(item.quantity)
            location_distribution[location_name]["total_value"] += _to_decimal(item.quantity) * _to_decimal(item.purchase_price)

        return {
            "total_stock_items": legacy_aggregate["total_items"] or 0,
            "total_locations": legacy_aggregate["total_locations"] or 0,
            "total_stock_value": _to_decimal(legacy_aggregate["total_stock_value"]),
            "location_distribution": [
                {
                    "location_name": location_name,
                    "item_count": values["item_count"],
                    "total_quantity": values["total_quantity"],
                    "total_value": values["total_value"],
                }
                for location_name, values in sorted(
                    location_distribution.items(),
                    key=lambda item: item[1]["total_quantity"],
                    reverse=True,
                )
            ],
            "aging_analysis": aging_analysis,
        }

    return {
        "total_stock_items": len(total_stock_items),
        "total_locations": len(total_locations),
        "total_stock_value": total_stock_value,
        "location_distribution": [
            {
                "location_name": location_name,
                "item_count": values["item_count"],
                "total_quantity": values["total_quantity"],
                "total_value": values["total_value"],
            }
            for location_name, values in sorted(
                location_distribution.items(),
                key=lambda item: item[1]["total_quantity"],
                reverse=True,
            )
        ],
        "aging_analysis": aging_analysis,
    }


def get_low_stock_rows(inventories):
    summary_map = get_inventory_summary_map(inventories)
    rows = []
    for inventory in inventories:
        summary = summary_map.get(inventory.id, {})
        current_stock = _to_decimal(summary.get("current_stock_level"))
        minimum_stock_level = _to_decimal(inventory.minimum_stock_level)
        if current_stock < minimum_stock_level:
            rows.append(
                {
                    "id": inventory.id,
                    "name": inventory.name,
                    "sku": inventory.external_system_id or "",
                    "quantity": current_stock,
                    "inventory_name": inventory.name,
                    "minimum_stock_level": minimum_stock_level,
                    "re_order_point": _to_decimal(inventory.re_order_point),
                    "shortfall": minimum_stock_level - current_stock,
                    "product_variant": "",
                    "display_image": None,
                }
            )
    rows.sort(key=lambda row: row["shortfall"], reverse=True)
    return rows
