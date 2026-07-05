# Generated manually on 2026-06-19

from django.db import migrations


def _resolve_structural_ancestor(stock_location_model, location):
    current = location
    visited_ids = set()
    while current is not None and current.id not in visited_ids:
        visited_ids.add(current.id)
        if getattr(current, "structural", False):
            return current
        parent_id = getattr(current, "parent_id", None)
        if parent_id is None:
            break
        current = stock_location_model.objects.filter(id=parent_id).first()
    return None


def _get_workspace_default_structural_location(stock_location_model, *, profile_id):
    root = (
        stock_location_model.objects.filter(profile_id=profile_id, structural=True, parent__isnull=True)
        .order_by("created_at", "id")
        .first()
    )
    if root is not None:
        return root
    return (
        stock_location_model.objects.filter(profile_id=profile_id, structural=True)
        .order_by("created_at", "id")
        .first()
    )


def backfill_inventory_placements(apps, schema_editor):
    inventory_item_model = apps.get_model("inventory", "InventoryItem")
    inventory_category_model = apps.get_model("inventory", "InventoryCategory")
    inventory_placement_model = apps.get_model("inventory", "InventoryPlacement")
    stock_location_model = apps.get_model("stock", "StockLocation")

    categories_by_id = {
        category.id: category
        for category in inventory_category_model.objects.all().only("id", "default_location_id")
    }
    default_roots_by_profile_id = {}

    for inventory_item in inventory_item_model.objects.all().iterator():
        profile_id = inventory_item.profile_id
        structural_location = None

        category = categories_by_id.get(inventory_item.inventory_category_id)
        if category and category.default_location_id:
            default_location = stock_location_model.objects.filter(id=category.default_location_id).first()
            if default_location is not None:
                structural_location = _resolve_structural_ancestor(stock_location_model, default_location)

        if structural_location is None:
            if profile_id not in default_roots_by_profile_id:
                default_roots_by_profile_id[profile_id] = _get_workspace_default_structural_location(
                    stock_location_model,
                    profile_id=profile_id,
                )
            structural_location = default_roots_by_profile_id.get(profile_id)

        if structural_location is None:
            continue

        inventory_placement_model.objects.get_or_create(
            inventory_item_id=inventory_item.id,
            structural_location_id=structural_location.id,
            defaults={
                "profile_id": profile_id,
                "location_name_snapshot": str(structural_location.name or ""),
                "active": True,
                "created_by_user_id": inventory_item.created_by_user_id,
                "updated_by_user_id": inventory_item.updated_by_user_id,
                "created_by_name": getattr(inventory_item, "created_by_name", "") or "",
                "updated_by_name": getattr(inventory_item, "updated_by_name", "") or "",
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0004_inventoryplacement"),
    ]

    operations = [
        migrations.RunPython(backfill_inventory_placements, migrations.RunPython.noop),
    ]
