from __future__ import annotations

import logging
import math
import uuid
from decimal import Decimal
from typing import Any

from django.db.models import Sum

from mainapps.inventory.models import InventoryItem, InventoryItemStatus
from mainapps.projections.models import CatalogVariantProjection
from mainapps.stock.models import StockBalance, StockReservation
from subapps.kafka.client import publish_event
from subapps.kafka.topics import (
    INVENTORY_AVAILABILITY_TOPIC,
    INVENTORY_FULFILLMENT_TOPIC,
    INVENTORY_RESERVATION_TOPIC,
)

logger = logging.getLogger(__name__)


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _decimal_aggregate(queryset, field_name: str) -> Decimal:
    return _to_decimal(queryset.aggregate(total=Sum(field_name))["total"] or 0)


def _coerce_threshold(value: Any) -> int | None:
    threshold = _to_decimal(value)
    if threshold <= 0:
        return None
    return int(math.ceil(float(threshold)))


def _resolve_catalog_variant(inventory_item: InventoryItem) -> CatalogVariantProjection | None:
    queryset = CatalogVariantProjection.objects.select_related("product").filter(profile_id=inventory_item.profile_id)

    if inventory_item.product_variant_id:
        variant = queryset.filter(variant_id=inventory_item.product_variant_id).first()
        if variant is not None:
            return variant

    candidate_values: list[str] = []
    for raw_value in [
        inventory_item.barcode_snapshot,
        (inventory_item.metadata or {}).get("legacy_variant_barcode"),
        inventory_item.sku_snapshot,
    ]:
        normalized = str(raw_value or "").strip()
        if normalized and normalized not in candidate_values:
            candidate_values.append(normalized)

    for lookup in candidate_values:
        variant = queryset.filter(variant_barcode=lookup).first()
        if variant is not None:
            return variant

        try:
            variant_uuid = uuid.UUID(lookup)
        except (AttributeError, TypeError, ValueError):
            variant_uuid = None
        if variant_uuid is not None:
            variant = queryset.filter(variant_id=variant_uuid).first()
            if variant is not None:
                return variant

        variant = queryset.filter(variant_sku=lookup).first()
        if variant is not None:
            return variant

    return None


def _sync_inventory_item_variant_fields(
    inventory_item: InventoryItem,
    variant: CatalogVariantProjection | None,
) -> None:
    if variant is None:
        return

    changed = False
    metadata = dict(inventory_item.metadata or {})
    if inventory_item.product_template_id != variant.product_id:
        inventory_item.product_template_id = variant.product_id
        changed = True
    if inventory_item.product_variant_id != variant.variant_id:
        inventory_item.product_variant_id = variant.variant_id
        changed = True
    if variant.variant_barcode and inventory_item.barcode_snapshot != variant.variant_barcode:
        inventory_item.barcode_snapshot = variant.variant_barcode
        metadata["legacy_variant_barcode"] = variant.variant_barcode
        changed = True
    if variant.variant_sku and inventory_item.sku_snapshot != variant.variant_sku:
        inventory_item.sku_snapshot = variant.variant_sku
        changed = True
    if changed:
        inventory_item.metadata = metadata
        inventory_item.save(update_fields=[
            "product_template_id",
            "product_variant_id",
            "barcode_snapshot",
            "sku_snapshot",
            "metadata",
            "updated_at",
        ])


def _derive_stock_status(inventory_item: InventoryItem, total_quantity: Decimal) -> str:
    if inventory_item.status == InventoryItemStatus.ARCHIVED:
        return "ARCHIVED"
    if inventory_item.status == InventoryItemStatus.DISCONTINUED:
        return "DISCONTINUED"
    if inventory_item.status == InventoryItemStatus.DRAFT:
        return "DRAFT"
    if total_quantity <= 0:
        return "OUT_OF_STOCK"
    if total_quantity <= _to_decimal(inventory_item.minimum_stock_level):
        return "LOW_STOCK"
    if total_quantity <= _to_decimal(inventory_item.reorder_point):
        return "REORDER_NEEDED"
    return "IN_STOCK"


def _build_availability_snapshot(inventory_item: InventoryItem) -> dict[str, Any] | None:
    variant = _resolve_catalog_variant(inventory_item)
    if variant is not None:
        _sync_inventory_item_variant_fields(inventory_item, variant)

    variant_id = variant.variant_id if variant is not None else inventory_item.product_variant_id
    if variant_id is None:
        logger.warning(
            "Skipping inventory event for inventory_item=%s because no catalog variant mapping was found.",
            inventory_item.id,
        )
        return None

    balances = StockBalance.objects.filter(
        profile_id=inventory_item.profile_id,
        inventory_item=inventory_item,
    )
    total_quantity = _decimal_aggregate(balances, "quantity_on_hand")
    reserved_quantity = _decimal_aggregate(balances, "quantity_reserved")
    available_quantity = _decimal_aggregate(balances, "quantity_available")
    low_stock_threshold = _coerce_threshold(
        inventory_item.minimum_stock_level if inventory_item.minimum_stock_level else inventory_item.reorder_point
    )

    return {
        "variant_id": str(variant_id),
        "product_id": str(variant.product_id) if variant is not None else (
            str(inventory_item.product_template_id) if inventory_item.product_template_id else ""
        ),
        "profile_id": inventory_item.profile_id,
        "inventory_item_id": str(inventory_item.id),
        "variant_barcode": (
            variant.variant_barcode if variant is not None else inventory_item.barcode_snapshot or None
        ),
        "variant_sku": (
            variant.variant_sku if variant is not None else inventory_item.sku_snapshot or ""
        ),
        "inventory_name": inventory_item.name_snapshot,
        "total_quantity": total_quantity,
        "reserved_quantity": reserved_quantity,
        "available_quantity": available_quantity,
        "low_stock_threshold": low_stock_threshold,
        "stock_status": _derive_stock_status(inventory_item, total_quantity),
        "track_stock": inventory_item.track_stock,
        "track_lot": inventory_item.track_lot,
        "track_serial": inventory_item.track_serial,
        "inventory_item_status": inventory_item.status,
    }


def _serialize_reservation(reservation: StockReservation) -> dict[str, Any]:
    return {
        "reservation_id": str(reservation.id),
        "status": reservation.status,
        "external_order_type": reservation.external_order_type,
        "external_order_id": reservation.external_order_id,
        "external_order_line_id": reservation.external_order_line_id or "",
        "stock_location_id": str(reservation.stock_location_id) if reservation.stock_location_id else "",
        "stock_lot_id": str(reservation.stock_lot_id) if reservation.stock_lot_id else "",
        "stock_serial_id": str(reservation.stock_serial_id) if reservation.stock_serial_id else "",
        "serial_number": reservation.stock_serial.serial_number if reservation.stock_serial_id else "",
        "reserved_quantity": reservation.reserved_quantity,
        "fulfilled_quantity": reservation.fulfilled_quantity,
        "remaining_quantity": reservation.remaining_quantity,
        "expires_at": reservation.expires_at.isoformat() if reservation.expires_at else None,
    }


def publish_inventory_availability_upserted(*, inventory_item_id) -> dict[str, Any] | None:
    inventory_item = InventoryItem.objects.filter(id=inventory_item_id).first()
    if inventory_item is None:
        logger.warning("Skipping inventory availability event because inventory_item=%s was not found.", inventory_item_id)
        return None

    payload = _build_availability_snapshot(inventory_item)
    if payload is None:
        return None

    return publish_event(
        INVENTORY_AVAILABILITY_TOPIC,
        "inventory.availability.upserted",
        payload,
        key=payload["variant_id"],
    )


def publish_inventory_reservation_upserted(*, reservation_id) -> dict[str, Any] | None:
    reservation = StockReservation.objects.select_related("inventory_item", "stock_serial").filter(id=reservation_id).first()
    if reservation is None:
        logger.warning("Skipping inventory reservation event because reservation=%s was not found.", reservation_id)
        return None

    payload = _build_availability_snapshot(reservation.inventory_item)
    if payload is None:
        return None
    payload["reservation"] = _serialize_reservation(reservation)

    return publish_event(
        INVENTORY_RESERVATION_TOPIC,
        "inventory.reservation.upserted",
        payload,
        key=payload["variant_id"],
    )


def publish_inventory_reservation_released(*, reservation_id) -> dict[str, Any] | None:
    reservation = StockReservation.objects.select_related("inventory_item", "stock_serial").filter(id=reservation_id).first()
    if reservation is None:
        logger.warning("Skipping inventory reservation release event because reservation=%s was not found.", reservation_id)
        return None

    payload = _build_availability_snapshot(reservation.inventory_item)
    if payload is None:
        return None
    payload["reservation"] = _serialize_reservation(reservation)

    return publish_event(
        INVENTORY_RESERVATION_TOPIC,
        "inventory.reservation.released",
        payload,
        key=payload["variant_id"],
    )


def publish_inventory_fulfillment_completed(*, reservation_id) -> dict[str, Any] | None:
    reservation = StockReservation.objects.select_related("inventory_item", "stock_serial").filter(id=reservation_id).first()
    if reservation is None:
        logger.warning("Skipping inventory fulfillment event because reservation=%s was not found.", reservation_id)
        return None

    payload = _build_availability_snapshot(reservation.inventory_item)
    if payload is None:
        return None
    payload["reservation"] = _serialize_reservation(reservation)

    return publish_event(
        INVENTORY_FULFILLMENT_TOPIC,
        "inventory.fulfillment.completed",
        payload,
        key=payload["variant_id"],
    )
