from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction

from mainapps.inventory.models import InventoryItem
from mainapps.stock.models import (
    StockBalance,
    StockLocation,
    StockLot,
    StockReservation,
    StockReservationStatus,
    StockSerial,
    StockSerialStatus,
)
from subapps.services.stock_domain import StockDomainError, StockDomainService


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _coerce_int(value: Any, field_name: str) -> int:
    if value in (None, ""):
        raise ValueError(f"{field_name} is required.")
    return int(value)


def _get_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValueError("POS event payload must be a JSON object.")
    return payload


def _iter_inventory_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("items") or []
    if not isinstance(raw_items, list):
        raise ValueError("POS event items payload must be a list.")
    return [item for item in raw_items if isinstance(item, dict) and item.get("inventory_item_id")]


def _resolve_item_context(profile_id: int, item_payload: dict[str, Any], *, quantity: Decimal):
    inventory_item = InventoryItem.objects.filter(
        id=item_payload["inventory_item_id"],
        profile_id=profile_id,
    ).first()
    if inventory_item is None:
        raise ValueError(f"Inventory item {item_payload.get('inventory_item_id')} was not found.")

    stock_lot = None
    stock_serial = None
    stock_location = None

    stock_serial_id = _as_str(item_payload.get("stock_serial_id"))
    if stock_serial_id:
        stock_serial = StockSerial.objects.select_related("stock_location", "stock_lot").filter(
            id=stock_serial_id,
            profile_id=profile_id,
            inventory_item=inventory_item,
        ).first()
        if stock_serial is None:
            raise ValueError(f"Stock serial {stock_serial_id} was not found for inventory item {inventory_item.id}.")
        stock_location = stock_serial.stock_location
        stock_lot = stock_serial.stock_lot
    elif inventory_item.track_serial:
        stock_serial = (
            StockSerial.objects.select_related("stock_location", "stock_lot")
            .filter(
                profile_id=profile_id,
                inventory_item=inventory_item,
                status=StockSerialStatus.AVAILABLE,
            )
            .order_by("created_at")
            .first()
        )
        if stock_serial is None:
            raise StockDomainError(f"No available serial found for inventory item {inventory_item.id}.")
        stock_location = stock_serial.stock_location
        stock_lot = stock_serial.stock_lot

    stock_lot_id = _as_str(item_payload.get("stock_lot_id"))
    if stock_lot is None and stock_lot_id:
        stock_lot = StockLot.objects.filter(
            id=stock_lot_id,
            profile_id=profile_id,
            inventory_item=inventory_item,
        ).first()
        if stock_lot is None:
            raise ValueError(f"Stock lot {stock_lot_id} was not found for inventory item {inventory_item.id}.")

    stock_location_id = _as_str(item_payload.get("stock_location_id"))
    if stock_location is None and stock_location_id:
        stock_location = StockLocation.objects.filter(id=stock_location_id, profile_id=profile_id).first()
        if stock_location is None:
            raise ValueError(f"Stock location {stock_location_id} was not found for profile {profile_id}.")

    if stock_location is None:
        balance = (
            StockBalance.objects.select_related("stock_location", "stock_lot")
            .filter(
                profile_id=profile_id,
                inventory_item=inventory_item,
                quantity_available__gte=quantity,
            )
            .order_by("stock_lot__expiry_date", "created_at")
            .first()
        )
        if balance is None:
            raise StockDomainError(f"No stock balance can satisfy quantity {quantity} for inventory item {inventory_item.id}.")
        stock_location = balance.stock_location
        if stock_lot is None:
            stock_lot = balance.stock_lot

    return inventory_item, stock_location, stock_lot, stock_serial


def _active_reservations(*, profile_id: int, order_id: str, item_id: str):
    return StockReservation.objects.select_related(
        "inventory_item",
        "stock_location",
        "stock_lot",
        "stock_serial",
    ).filter(
        profile_id=profile_id,
        external_order_type="pos_order_item",
        external_order_id=order_id,
        external_order_line_id=item_id,
        status__in=[StockReservationStatus.ACTIVE, StockReservationStatus.PARTIALLY_FULFILLED],
    ).order_by("created_at")


def _handle_reservation_requested(payload: dict[str, Any]) -> bool:
    profile_id = _coerce_int(payload.get("profile_id"), "profile_id")
    order_id = _as_str(payload.get("order_id"))
    actor_user_id = payload.get("actor_user_id")
    notes = _as_str(payload.get("notes"))
    order_number = _as_str(payload.get("order_number"))

    with transaction.atomic():
        for item_payload in _iter_inventory_items(payload):
            item_id = _as_str(item_payload.get("item_id"))
            if not item_id:
                continue
            requested_quantity = _to_decimal(item_payload.get("requested_quantity") or item_payload.get("ordered_quantity"))
            if requested_quantity <= 0:
                continue
            if _active_reservations(profile_id=profile_id, order_id=order_id, item_id=item_id).exists():
                continue

            inventory_item, stock_location, stock_lot, stock_serial = _resolve_item_context(
                profile_id,
                item_payload,
                quantity=requested_quantity,
            )
            StockDomainService.reserve_stock(
                inventory_item=inventory_item,
                stock_location=stock_location,
                quantity=requested_quantity,
                external_order_type="pos_order_item",
                external_order_id=order_id,
                external_order_line_id=item_id,
                actor_user_id=actor_user_id,
                stock_lot=stock_lot,
                stock_serial=stock_serial,
                notes=notes or f"Reserved for POS order {order_number or order_id}",
            )
    return True


def _handle_reservation_released(payload: dict[str, Any]) -> bool:
    profile_id = _coerce_int(payload.get("profile_id"), "profile_id")
    order_id = _as_str(payload.get("order_id"))
    actor_user_id = payload.get("actor_user_id")
    notes = _as_str(payload.get("notes")) or f"Released reservation for POS order {payload.get('order_number') or order_id}"

    with transaction.atomic():
        for item_payload in _iter_inventory_items(payload):
            item_id = _as_str(item_payload.get("item_id"))
            if not item_id:
                continue
            release_quantity = _to_decimal(item_payload.get("requested_quantity") or item_payload.get("reserved_quantity"))
            if release_quantity <= 0:
                continue

            remaining_to_release = release_quantity
            for reservation in _active_reservations(profile_id=profile_id, order_id=order_id, item_id=item_id):
                if remaining_to_release <= 0:
                    break
                quantity = min(remaining_to_release, _to_decimal(reservation.remaining_quantity))
                if quantity <= 0:
                    continue
                StockDomainService.release_reservation(
                    reservation=reservation,
                    quantity=quantity,
                    actor_user_id=actor_user_id,
                    notes=notes,
                )
                remaining_to_release -= quantity
    return True


def _handle_fulfillment_confirmed(payload: dict[str, Any]) -> bool:
    profile_id = _coerce_int(payload.get("profile_id"), "profile_id")
    order_id = _as_str(payload.get("order_id"))
    actor_user_id = payload.get("actor_user_id")
    notes = _as_str(payload.get("notes")) or f"Fulfilled POS order {payload.get('order_number') or order_id}"

    with transaction.atomic():
        for item_payload in _iter_inventory_items(payload):
            item_id = _as_str(item_payload.get("item_id"))
            if not item_id:
                continue
            fulfill_quantity = _to_decimal(item_payload.get("requested_quantity") or item_payload.get("ordered_quantity"))
            if fulfill_quantity <= 0:
                continue

            reservation_reference = _as_str(item_payload.get("reservation_reference"))
            if reservation_reference:
                reservations = StockReservation.objects.select_related(
                    "inventory_item",
                    "stock_location",
                    "stock_lot",
                    "stock_serial",
                ).filter(
                    id=reservation_reference,
                    profile_id=profile_id,
                    external_order_type="pos_order_item",
                    external_order_id=order_id,
                )
            else:
                reservations = _active_reservations(profile_id=profile_id, order_id=order_id, item_id=item_id)

            remaining_to_fulfill = fulfill_quantity
            for reservation in reservations.order_by("created_at"):
                if remaining_to_fulfill <= 0:
                    break
                quantity = min(remaining_to_fulfill, _to_decimal(reservation.remaining_quantity))
                if quantity <= 0:
                    continue
                StockDomainService.fulfill_reservation(
                    reservation=reservation,
                    quantity=quantity,
                    actor_user_id=actor_user_id,
                    notes=notes,
                )
                remaining_to_fulfill -= quantity
    return True


def _handle_order_cancelled(payload: dict[str, Any]) -> bool:
    profile_id = _coerce_int(payload.get("profile_id"), "profile_id")
    order_id = _as_str(payload.get("order_id"))
    actor_user_id = payload.get("actor_user_id")
    notes = f"Released reservations for cancelled POS order {payload.get('order_number') or order_id}"

    with transaction.atomic():
        reservations = StockReservation.objects.select_related(
            "inventory_item",
            "stock_location",
            "stock_lot",
            "stock_serial",
        ).filter(
            profile_id=profile_id,
            external_order_type="pos_order_item",
            external_order_id=order_id,
            status__in=[StockReservationStatus.ACTIVE, StockReservationStatus.PARTIALLY_FULFILLED],
        )
        for reservation in reservations:
            remaining_quantity = _to_decimal(reservation.remaining_quantity)
            if remaining_quantity <= 0:
                continue
            StockDomainService.release_reservation(
                reservation=reservation,
                quantity=remaining_quantity,
                actor_user_id=actor_user_id,
                notes=notes,
            )
    return True


def handle_pos_order_event(envelope: dict[str, Any], **_: Any) -> bool:
    event_name = _as_str(envelope.get("event_name"))
    payload = _get_payload(envelope)

    if event_name == "pos.inventory.reservation.requested":
        return _handle_reservation_requested(payload)
    if event_name == "pos.inventory.reservation.released":
        return _handle_reservation_released(payload)
    if event_name == "pos.inventory.fulfillment.confirmed":
        return _handle_fulfillment_confirmed(payload)
    if event_name == "pos.order.cancelled":
        return _handle_order_cancelled(payload)

    return True
