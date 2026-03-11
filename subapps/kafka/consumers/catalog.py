from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.db import transaction

from mainapps.projections.models import CatalogProductProjection, CatalogVariantProjection


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

    _invalidate_variant_cache(existing_barcode, payload.get("variant_barcode"), str(variant.variant_id))
    return True
