from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from django.db.models import Sum

from mainapps.inventory.models import InventoryItem, InventoryItemStatus
from mainapps.orders.models import GoodsReceiptLine
from mainapps.projections.models import CatalogVariantProjection
from mainapps.stock.models import StockBalance, StockReservation
from subapps.kafka.client import publish_event
from subapps.kafka.producers.platform_events import build_audit_envelope
from subapps.services.location_scope import get_workspace_default_structural_location, resolve_structural_location
from subapps.kafka.topics import (
    INVENTORY_AVAILABILITY_TOPIC,
    INVENTORY_FULFILLMENT_TOPIC,
    INVENTORY_PURCHASE_PRICE_TOPIC,
    INVENTORY_RESERVATION_TOPIC,
)

logger = logging.getLogger(__name__)


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _string(value: Any) -> str:
    return str(value or "").strip()


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


def _build_purchase_price_snapshot(goods_receipt_line: GoodsReceiptLine) -> dict[str, Any] | None:
    inventory_item = goods_receipt_line.inventory_item
    variant = _resolve_catalog_variant(inventory_item)
    if variant is not None:
        _sync_inventory_item_variant_fields(inventory_item, variant)

    variant_id = variant.variant_id if variant is not None else inventory_item.product_variant_id
    if variant_id is None:
        logger.warning(
            "Skipping inventory purchase-price event for goods_receipt_line=%s because no catalog variant mapping was found.",
            goods_receipt_line.id,
        )
        return None

    goods_receipt = goods_receipt_line.goods_receipt
    purchase_order = goods_receipt.purchase_order if goods_receipt else None
    supplier_name = ""
    if goods_receipt and goods_receipt.supplier_id and getattr(goods_receipt.supplier, "name", None):
        supplier_name = goods_receipt.supplier.name
    elif purchase_order and purchase_order.supplier_id and getattr(purchase_order.supplier, "name", None):
        supplier_name = purchase_order.supplier.name

    return {
        "variant_id": str(variant_id),
        "product_id": str(variant.product_id) if variant is not None else (
            str(inventory_item.product_template_id) if inventory_item.product_template_id else ""
        ),
        "profile_id": inventory_item.profile_id,
        "inventory_item_id": str(inventory_item.id),
        "goods_receipt_line_id": str(goods_receipt_line.id),
        "goods_receipt_reference": goods_receipt.reference if goods_receipt else "",
        "purchase_order_reference": purchase_order.reference if purchase_order else "",
        "variant_barcode": (
            variant.variant_barcode if variant is not None else inventory_item.barcode_snapshot or None
        ),
        "variant_sku": variant.variant_sku if variant is not None else inventory_item.sku_snapshot or "",
        "purchase_price": goods_receipt_line.unit_cost,
        "quantity_purchased": goods_receipt_line.received_quantity,
        "currency": purchase_order.order_currency if purchase_order else "",
        "supplier": supplier_name,
        "received_at": goods_receipt.received_at.isoformat() if goods_receipt and goods_receipt.received_at else None,
        "purchase_order_line_id": (
            str(goods_receipt_line.purchase_order_line_id) if goods_receipt_line.purchase_order_line_id else ""
        ),
    }


def _derive_stock_status(inventory_item: InventoryItem, total_quantity: Decimal) -> str:
    minimum_stock_level = _to_decimal(inventory_item.minimum_stock_level)
    reorder_point = _to_decimal(inventory_item.reorder_point)

    if inventory_item.status == InventoryItemStatus.ARCHIVED:
        return "ARCHIVED"
    if inventory_item.status == InventoryItemStatus.DISCONTINUED:
        return "DISCONTINUED"
    if inventory_item.status == InventoryItemStatus.DRAFT:
        return "DRAFT"
    if total_quantity <= 0:
        return "OUT_OF_STOCK"
    if minimum_stock_level > 0 and total_quantity <= minimum_stock_level:
        return "LOW_STOCK"
    if reorder_point > 0 and total_quantity <= reorder_point:
        return "REORDER_NEEDED"
    return "IN_STOCK"


def _availability_threshold(inventory_item: InventoryItem) -> int | None:
    minimum_stock_level = _to_decimal(inventory_item.minimum_stock_level)
    reorder_point = _to_decimal(inventory_item.reorder_point)

    if minimum_stock_level > 0:
        return _coerce_threshold(minimum_stock_level)
    if reorder_point > 0:
        return _coerce_threshold(reorder_point)
    return None


def _build_location_breakdown(
    inventory_item: InventoryItem,
    balances,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for balance in balances:
        structural_location = resolve_structural_location(
            profile_id=inventory_item.profile_id,
            stock_location=balance.stock_location,
        )
        if structural_location is None:
            continue

        key = str(structural_location.id)
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "structural_location_id": key,
                "location_name": str(structural_location.name or ""),
                "inventory_item_id": str(inventory_item.id),
                "total_quantity": Decimal("0"),
                "reserved_quantity": Decimal("0"),
                "available_quantity": Decimal("0"),
            }
            grouped[key] = entry

        entry["total_quantity"] += _to_decimal(balance.quantity_on_hand)
        entry["reserved_quantity"] += _to_decimal(balance.quantity_reserved)
        entry["available_quantity"] += _to_decimal(balance.quantity_available)

    for placement in inventory_item.placements.select_related("structural_location").filter(active=True):
        key = str(placement.structural_location_id)
        grouped.setdefault(
            key,
            {
                "structural_location_id": key,
                "location_name": placement.location_name_snapshot or str(placement.structural_location.name or ""),
                "inventory_item_id": str(inventory_item.id),
                "total_quantity": Decimal("0"),
                "reserved_quantity": Decimal("0"),
                "available_quantity": Decimal("0"),
            },
        )

    if not grouped:
        default_structural_location = get_workspace_default_structural_location(profile_id=inventory_item.profile_id)
        if default_structural_location is not None:
            grouped[str(default_structural_location.id)] = {
                "structural_location_id": str(default_structural_location.id),
                "location_name": str(default_structural_location.name or ""),
                "inventory_item_id": str(inventory_item.id),
                "total_quantity": Decimal("0"),
                "reserved_quantity": Decimal("0"),
                "available_quantity": Decimal("0"),
            }

    ordered = sorted(
        grouped.values(),
        key=lambda item: (item["available_quantity"], item["total_quantity"]),
        reverse=True,
    )
    return [
        {
            **item,
            "stock_status": _derive_stock_status(inventory_item, item["total_quantity"]),
        }
        for item in ordered
    ]


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
    low_stock_threshold = _availability_threshold(inventory_item)
    location_breakdown = _build_location_breakdown(inventory_item, balances)

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
        "location_breakdown": location_breakdown,
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


def _availability_summary(payload: dict[str, Any]) -> str:
    inventory_name = _string(payload.get("inventory_name")) or "Inventory item"
    stock_status = _string(payload.get("stock_status")).upper()
    if stock_status == "OUT_OF_STOCK":
        return f"{inventory_name} is out of stock"
    if stock_status == "LOW_STOCK":
        return f"{inventory_name} is low on stock"
    if stock_status == "REORDER_NEEDED":
        return f"{inventory_name} has reached its reorder threshold"
    return f"{inventory_name} inventory availability updated"


def _availability_severity(payload: dict[str, Any]) -> str:
    stock_status = _string(payload.get("stock_status")).upper()
    if stock_status in {"OUT_OF_STOCK", "LOW_STOCK", "REORDER_NEEDED"}:
        return "warning"
    return "info"


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
        envelope_overrides=build_audit_envelope(
            workspace_id=_string(payload.get("profile_id")),
            actor={"user_id": _string(inventory_item.updated_by_user_id or inventory_item.created_by_user_id)},
            target={
                "type": "inventory_item",
                "id": _string(payload.get("inventory_item_id")),
                "label": _string(payload.get("inventory_name")),
                "barcode": _string(payload.get("variant_barcode")),
                "sku": _string(payload.get("variant_sku")),
            },
            summary=_availability_summary(payload),
            severity=_availability_severity(payload),
            metadata={
                "stock_status": payload.get("stock_status"),
                "total_quantity": payload.get("total_quantity"),
                "available_quantity": payload.get("available_quantity"),
                "reserved_quantity": payload.get("reserved_quantity"),
                "low_stock_threshold": payload.get("low_stock_threshold"),
                "location_breakdown": payload.get("location_breakdown"),
            },
            feature_area="inventory",
        ),
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
        envelope_overrides=build_audit_envelope(
            workspace_id=_string(payload.get("profile_id")),
            actor={"user_id": _string(reservation.updated_by_user_id or reservation.created_by_user_id)},
            target={
                "type": "reservation",
                "id": _string(payload["reservation"].get("reservation_id")),
                "label": _string(payload.get("inventory_name")),
                "barcode": _string(payload.get("variant_barcode")),
                "sku": _string(payload.get("variant_sku")),
                "reference_number": _string(payload["reservation"].get("external_order_id")),
                "location_id": _string(payload["reservation"].get("stock_location_id")),
            },
            summary=(
                f"Reserved {_string(payload['reservation'].get('reserved_quantity'))} of "
                f"{_string(payload.get('inventory_name')) or 'inventory item'}"
            ),
            metadata={"reservation": payload.get("reservation"), "stock_status": payload.get("stock_status")},
            feature_area="inventory",
            reference_number=_string(payload["reservation"].get("external_order_id")),
        ),
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
        envelope_overrides=build_audit_envelope(
            workspace_id=_string(payload.get("profile_id")),
            actor={"user_id": _string(reservation.updated_by_user_id or reservation.created_by_user_id)},
            target={
                "type": "reservation",
                "id": _string(payload["reservation"].get("reservation_id")),
                "label": _string(payload.get("inventory_name")),
                "barcode": _string(payload.get("variant_barcode")),
                "sku": _string(payload.get("variant_sku")),
                "reference_number": _string(payload["reservation"].get("external_order_id")),
                "location_id": _string(payload["reservation"].get("stock_location_id")),
            },
            summary=f"Released reservation for {_string(payload.get('inventory_name')) or 'inventory item'}",
            severity="warning",
            metadata={"reservation": payload.get("reservation"), "stock_status": payload.get("stock_status")},
            feature_area="inventory",
            reference_number=_string(payload["reservation"].get("external_order_id")),
        ),
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
        envelope_overrides=build_audit_envelope(
            workspace_id=_string(payload.get("profile_id")),
            actor={"user_id": _string(reservation.updated_by_user_id or reservation.created_by_user_id)},
            target={
                "type": "reservation",
                "id": _string(payload["reservation"].get("reservation_id")),
                "label": _string(payload.get("inventory_name")),
                "barcode": _string(payload.get("variant_barcode")),
                "sku": _string(payload.get("variant_sku")),
                "reference_number": _string(payload["reservation"].get("external_order_id")),
                "location_id": _string(payload["reservation"].get("stock_location_id")),
            },
            summary=(
                f"Fulfillment completed for {_string(payload.get('inventory_name')) or 'inventory item'}"
            ),
            metadata={"reservation": payload.get("reservation"), "stock_status": payload.get("stock_status")},
            feature_area="inventory",
            reference_number=_string(payload["reservation"].get("external_order_id")),
        ),
    )


def publish_inventory_purchase_price_recorded(*, goods_receipt_line_id) -> dict[str, Any] | None:
    goods_receipt_line = (
        GoodsReceiptLine.objects
        .select_related(
            "inventory_item",
            "goods_receipt",
            "goods_receipt__purchase_order",
            "goods_receipt__supplier",
            "goods_receipt__purchase_order__supplier",
        )
        .filter(id=goods_receipt_line_id)
        .first()
    )
    if goods_receipt_line is None:
        logger.warning(
            "Skipping inventory purchase-price event because goods_receipt_line=%s was not found.",
            goods_receipt_line_id,
        )
        return None

    payload = _build_purchase_price_snapshot(goods_receipt_line)
    if payload is None:
        return None

    return publish_event(
        INVENTORY_PURCHASE_PRICE_TOPIC,
        "inventory.purchase_price.recorded",
        payload,
        key=payload["variant_id"],
        envelope_overrides=build_audit_envelope(
            workspace_id=_string(payload.get("profile_id")),
            actor={
                "user_id": _string(
                    goods_receipt_line.updated_by_user_id
                    or goods_receipt_line.created_by_user_id
                    or getattr(goods_receipt_line.goods_receipt, "updated_by_user_id", None)
                    or getattr(goods_receipt_line.goods_receipt, "created_by_user_id", None)
                )
            },
            target={
                "type": "goods_receipt_line",
                "id": _string(payload.get("goods_receipt_line_id")),
                "label": _string(payload.get("goods_receipt_reference")),
                "barcode": _string(payload.get("variant_barcode")),
                "sku": _string(payload.get("variant_sku")),
                "reference_number": _string(payload.get("purchase_order_reference") or payload.get("goods_receipt_reference")),
            },
            summary=(
                f"Recorded purchase price for {_string(payload.get('variant_sku') or payload.get('variant_barcode') or payload.get('goods_receipt_reference'))}"
            ),
            metadata={
                "purchase_price": payload.get("purchase_price"),
                "quantity_purchased": payload.get("quantity_purchased"),
                "supplier": payload.get("supplier"),
                "currency": payload.get("currency"),
            },
            feature_area="inventory",
            reference_number=_string(payload.get("purchase_order_reference") or payload.get("goods_receipt_reference")),
        ),
    )
