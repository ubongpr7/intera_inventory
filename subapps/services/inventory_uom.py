from __future__ import annotations

from decimal import Decimal

from mainapps.inventory.models import InventoryItem, InventoryItemUnit


def ensure_base_inventory_unit(
    inventory_item: InventoryItem,
    *,
    preferred_code: str = "",
    created_by_user_id: int | None = None,
) -> InventoryItemUnit:
    """Ensure every inventory item has one canonical, saleable base unit.

    Catalog projections can arrive after the UOM migration and historically
    created items with blank UOM fields. POS always needs a concrete unit code,
    so repair the legacy representation at the point the item is created or
    synchronized instead of failing a sale later.
    """
    code = (
        (inventory_item.stock_uom_code or "").strip()
        or (inventory_item.default_uom_code or "").strip()
        or (preferred_code or "").strip()
        or "unit"
    )
    changed_fields: list[str] = []
    if not (inventory_item.stock_uom_code or "").strip():
        inventory_item.stock_uom_code = code
        changed_fields.append("stock_uom_code")
    if not (inventory_item.default_uom_code or "").strip():
        inventory_item.default_uom_code = code
        changed_fields.append("default_uom_code")
    if created_by_user_id is not None:
        inventory_item.updated_by_user_id = created_by_user_id
        changed_fields.append("updated_by_user_id")
    if changed_fields:
        inventory_item.save(update_fields=[*dict.fromkeys(changed_fields), "updated_at"])

    unit, _ = InventoryItemUnit.objects.get_or_create(
        inventory_item=inventory_item,
        code=code,
        defaults={
            "profile_id": inventory_item.profile_id,
            "label": code,
            "conversion_to_stock": Decimal("1"),
            "is_purchase_unit": True,
            "is_sale_unit": True,
            "allows_partial": False,
            "decimal_places": 0,
        },
    )
    return unit
