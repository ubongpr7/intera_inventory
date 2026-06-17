from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.db import transaction

from mainapps.inventory.models import InventoryItem, InventoryItemStatus
from mainapps.projections.models import CatalogProductProjection, CatalogVariantProjection
from subapps.kafka.producers.inventory import publish_inventory_availability_upserted


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _product_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": int(payload["profile_id"]),
        "name": payload.get("name", "") or "",
        "category_name": payload.get("category_name", "") or "",
        "tax_rate": payload.get("tax_rate", 0) or 0,
        "track_stock": _coerce_bool(payload.get("track_stock"), True),
        "is_active": _coerce_bool(payload.get("is_active"), True),
    }


def _upsert_product_projection(payload: dict[str, Any]) -> CatalogProductProjection:
    product, _ = CatalogProductProjection.objects.update_or_create(
        product_id=payload["product_id"],
        defaults=_product_defaults(payload),
    )
    return product


def _invalidate_variant_cache(*keys: str | None) -> None:
    for key in keys:
        if not key:
            continue
        cache.delete(f"product_variant_projection_{key}")


def _variant_should_track_stock(variant: CatalogVariantProjection) -> bool:
    return bool(variant.is_active and variant.product and variant.product.track_stock)


def _ensure_inventory_item_for_variant(variant: CatalogVariantProjection) -> InventoryItem | None:
    """Create/update a zero-stock inventory item for a catalog variant.

    Stock quantities are intentionally not created here. Quantity remains driven by
    receiving, adjustment, reservation, and fulfillment operations.
    """
    if not _variant_should_track_stock(variant):
        return None

    metadata = {
        "source": "catalog_variant_projection",
        "catalog_variant_id": str(variant.variant_id),
        "catalog_product_id": str(variant.product_id),
        "catalog_image_url": variant.image_url or "",
    }
    defaults = {
        "product_template_id": variant.product_id,
        "name_snapshot": variant.display_name or variant.variant_name or variant.product.name,
        "sku_snapshot": variant.variant_sku or "",
        "barcode_snapshot": variant.variant_barcode or "",
        "product_variant_image_url": variant.image_url or "",
        "inventory_type": "finished_good",
        "track_stock": True,
        "status": InventoryItemStatus.ACTIVE,
        "metadata": metadata,
    }

    inventory_item, created = InventoryItem.objects.get_or_create(
        profile_id=variant.profile_id,
        product_variant_id=variant.variant_id,
        defaults=defaults,
    )

    if not created:
        changed_fields: list[str] = []
        for field_name, value in defaults.items():
            if field_name == "metadata":
                merged_metadata = {**(inventory_item.metadata or {}), **metadata}
                if inventory_item.metadata != merged_metadata:
                    inventory_item.metadata = merged_metadata
                    changed_fields.append("metadata")
                continue
            if getattr(inventory_item, field_name) != value:
                setattr(inventory_item, field_name, value)
                changed_fields.append(field_name)
        if changed_fields:
            inventory_item.save(update_fields=[*changed_fields, "updated_at"])

    return inventory_item


def handle_catalog_product_event(envelope: dict[str, Any], **_: Any) -> bool:
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("Catalog product payload must be a JSON object.")

    defaults = _product_defaults(payload)
    if envelope.get("event_name") == "catalog.product.deleted":
        defaults["is_active"] = False

    product, _created = CatalogProductProjection.objects.update_or_create(
        product_id=payload["product_id"],
        defaults=defaults,
    )

    if envelope.get("event_name") == "catalog.product.deleted":
        CatalogVariantProjection.objects.filter(product=product).update(is_active=False, pos_visible=False)

    return True


def handle_catalog_variant_event(envelope: dict[str, Any], **_: Any) -> bool:
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("Catalog variant payload must be a JSON object.")

    existing_barcode = CatalogVariantProjection.objects.filter(variant_id=payload["variant_id"]).values_list(
        "variant_barcode",
        flat=True,
    ).first()

    product_payload = payload.get("product") if isinstance(payload.get("product"), dict) else None

    with transaction.atomic():
        if product_payload is not None:
            product = _upsert_product_projection(product_payload)
        else:
            product = CatalogProductProjection.objects.get(product_id=payload["product_id"])

        defaults = {
            "product": product,
            "profile_id": int(payload["profile_id"]),
            "display_name": payload.get("display_name", "") or "",
            "variant_name": payload.get("variant_name", "") or "",
            "variant_barcode": payload.get("variant_barcode"),
            "variant_sku": payload.get("variant_sku", "") or "",
            "image_url": payload.get("image_url", "") or "",
            "sales_price": payload.get("sales_price", 0) or 0,
            "is_active": _coerce_bool(payload.get("is_active"), True),
            "pos_visible": _coerce_bool(payload.get("pos_visible"), True),
        }

        if envelope.get("event_name") == "catalog.variant.deleted":
            defaults["is_active"] = False
            defaults["pos_visible"] = False

        variant, _created = CatalogVariantProjection.objects.update_or_create(
            variant_id=payload["variant_id"],
            defaults=defaults,
        )

        inventory_item = None
        if envelope.get("event_name") != "catalog.variant.deleted":
            inventory_item = _ensure_inventory_item_for_variant(variant)

        if inventory_item is not None:
            transaction.on_commit(
                lambda item_id=inventory_item.id: publish_inventory_availability_upserted(
                    inventory_item_id=item_id
                )
            )

    _invalidate_variant_cache(existing_barcode, payload.get("variant_barcode"), str(variant.variant_id))
    return True
