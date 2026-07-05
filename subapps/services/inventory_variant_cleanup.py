from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from django.db.models import QuerySet

from mainapps.inventory.models import InventoryItem


@dataclass
class InventoryItemDeleteOutcome:
    inventory_item_id: str
    deleted: bool
    blocked_by_stock: bool = False
    blocked_relations: dict[str, int] = field(default_factory=dict)
    reason: str = ""


def inventory_item_has_non_zero_stock(inventory_item: InventoryItem) -> bool:
    for balance in inventory_item.stock_balances.all():
        if (
            balance.quantity_on_hand != 0
            or balance.quantity_reserved != 0
            or balance.quantity_available != 0
        ):
            return True
    return False


def get_inventory_item_delete_blockers(inventory_item: InventoryItem) -> dict[str, int]:
    blockers: dict[str, int] = {}
    relation_counts = {
        "purchase_order_lines": inventory_item.purchase_order_lines.count(),
        "goods_receipt_lines": inventory_item.goods_receipt_lines.count(),
        "sales_order_lines": inventory_item.sales_order_lines.count(),
        "stock_movements": inventory_item.stock_movements.count(),
        "stock_reservations": inventory_item.stock_reservations.count(),
        "stock_lots": inventory_item.stock_lots.count(),
        "stock_serials": inventory_item.stock_serials.count(),
    }
    for label, count in relation_counts.items():
        if count > 0:
            blockers[label] = count
    return blockers


def delete_inventory_item_if_safe(inventory_item: InventoryItem) -> InventoryItemDeleteOutcome:
    outcome = InventoryItemDeleteOutcome(inventory_item_id=str(inventory_item.id), deleted=False)
    if inventory_item_has_non_zero_stock(inventory_item):
        outcome.blocked_by_stock = True
        outcome.reason = "inventory item still has non-zero stock"
        return outcome

    blockers = get_inventory_item_delete_blockers(inventory_item)
    if blockers:
        outcome.blocked_relations = blockers
        outcome.reason = "inventory item still has protected downstream records"
        return outcome

    inventory_item.delete()
    outcome.deleted = True
    return outcome


def is_catalog_sourced_inventory_item(inventory_item: InventoryItem) -> bool:
    metadata = inventory_item.metadata or {}
    return (
        metadata.get("source") == "catalog_variant_projection"
        or inventory_item.product_variant_id is not None
        or inventory_item.product_template_id is not None
    )


def find_catalog_orphan_inventory_items(
    *,
    profile_id: int,
    valid_variant_ids: Iterable[str],
) -> QuerySet[InventoryItem]:
    valid_variant_id_set = {str(value) for value in valid_variant_ids if value}
    queryset = (
        InventoryItem.objects
        .prefetch_related(
            "stock_balances",
            "purchase_order_lines",
            "goods_receipt_lines",
            "sales_order_lines",
            "stock_movements",
            "stock_reservations",
            "stock_lots",
            "stock_serials",
        )
        .filter(profile_id=profile_id)
        .order_by("created_at", "id")
    )

    orphan_ids: list[str] = []
    for inventory_item in queryset:
        if not is_catalog_sourced_inventory_item(inventory_item):
            continue
        variant_id = str(inventory_item.product_variant_id) if inventory_item.product_variant_id else ""
        if variant_id and variant_id in valid_variant_id_set:
            continue
        orphan_ids.append(str(inventory_item.id))

    return InventoryItem.objects.filter(id__in=orphan_ids).prefetch_related(
        "stock_balances",
        "purchase_order_lines",
        "goods_receipt_lines",
        "sales_order_lines",
        "stock_movements",
        "stock_reservations",
        "stock_lots",
        "stock_serials",
    )
