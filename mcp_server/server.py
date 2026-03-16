from __future__ import annotations

import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django
from django.apps import apps
from asgiref.sync import sync_to_async

if not apps.ready:
    django.setup()

from django.db.models import Q, QuerySet
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from rest_framework_simplejwt.tokens import UntypedToken
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
import uvicorn

from mainapps.inventory.models import Inventory, InventoryItem
from mainapps.stock.models import StockLocation, StockMovement, StockReservation, StockSerial, StockLot
from subapps.services.inventory_read_model import (
    get_inventory_item_summary_map,
    get_inventory_summary_map,
    get_location_stock_summary,
    get_profile_stock_analytics,
)
from subapps.utils.request_context import coerce_identity_id, scope_queryset_by_identity


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _extract_bearer_token(header_value: str | None) -> str | None:
    if not header_value:
        return None
    parts = header_value.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _to_json_compatible(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_json_compatible(item) for item in value]
    return value


@dataclass(slots=True)
class InventoryMcpPrincipal:
    token: str
    claims: dict[str, Any]
    user_id: str
    profile_id: int
    company_code: str | None
    permissions: set[str]


_principal_var: ContextVar[InventoryMcpPrincipal | None] = ContextVar(
    "inventory_mcp_principal",
    default=None,
)


def get_current_principal(*, required: bool = False) -> InventoryMcpPrincipal | None:
    principal = _principal_var.get()
    if principal is None and required:
        raise RuntimeError("This MCP tool requires a valid bearer token with a profile_id claim.")
    return principal


def _build_principal_from_token(token: str) -> InventoryMcpPrincipal:
    claims = dict(UntypedToken(token).payload)
    user_id = claims.get("user_id") or claims.get("id") or claims.get("sub")
    if user_id in (None, ""):
        raise RuntimeError("Access token missing user identifier.")
    profile_id = coerce_identity_id(claims.get("profile_id"))
    if profile_id is None:
        raise RuntimeError("Access token missing profile_id claim.")
    permissions = claims.get("permissions") or []
    if not isinstance(permissions, list):
        permissions = list(permissions)
    return InventoryMcpPrincipal(
        token=token,
        claims=claims,
        user_id=str(user_id),
        profile_id=profile_id,
        company_code=(str(claims["company_code"]).strip() if claims.get("company_code") else None),
        permissions={str(item) for item in permissions if str(item).strip()},
    )


class InventoryMcpAuthMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path == "/health":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        auth_header = headers.get("authorization")
        if not auth_header:
            await self.app(scope, receive, send)
            return

        token = _extract_bearer_token(auth_header)
        if token is None:
            response = JSONResponse({"detail": "Invalid Authorization header."}, status_code=401)
            await response(scope, receive, send)
            return

        try:
            principal = _build_principal_from_token(token)
        except Exception as exc:
            response = JSONResponse({"detail": str(exc)}, status_code=401)
            await response(scope, receive, send)
            return

        reset_token = _principal_var.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            _principal_var.reset(reset_token)


def _stringify(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _inventory_payload(inventory: Inventory, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_summary = summary or {}
    return {
        "id": str(inventory.id),
        "name": inventory.name,
        "external_system_id": inventory.external_system_id,
        "description": inventory.description or "",
        "inventory_type": inventory.inventory_type,
        "category": inventory.category.name if inventory.category_id and inventory.category else None,
        "unit_name": inventory.unit_name,
        "active": inventory.active,
        "trackable": inventory.trackable,
        "batch_tracking_enabled": inventory.batch_tracking_enabled,
        "automate_reorder": inventory.automate_reorder,
        "minimum_stock_level": _decimal_to_float(inventory.minimum_stock_level),
        "re_order_point": _decimal_to_float(inventory.re_order_point),
        "re_order_quantity": _decimal_to_float(inventory.re_order_quantity),
        "current_stock_level": _decimal_to_float(resolved_summary.get("current_stock_level", Decimal("0"))),
        "quantity_reserved": _decimal_to_float(resolved_summary.get("quantity_reserved", Decimal("0"))),
        "quantity_available": _decimal_to_float(resolved_summary.get("quantity_available", Decimal("0"))),
        "total_stock_value": _decimal_to_float(resolved_summary.get("total_stock_value", Decimal("0"))),
        "stock_status": resolved_summary.get("stock_status") or inventory.stock_status,
        "total_locations": resolved_summary.get("total_locations", 0),
        "expiring_soon_count": resolved_summary.get("expiring_soon_count", 0),
        "location_breakdown": _to_json_compatible(resolved_summary.get("location_breakdown", [])),
        "expiring_lots": _to_json_compatible(resolved_summary.get("expiring_lots", [])),
    }


def _inventory_item_payload(inventory_item: InventoryItem, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_summary = summary or {}
    return {
        "id": str(inventory_item.id),
        "name": inventory_item.name_snapshot,
        "sku": inventory_item.sku_snapshot,
        "barcode": inventory_item.barcode_snapshot,
        "description": inventory_item.description,
        "inventory_type": inventory_item.inventory_type,
        "inventory_category": (
            inventory_item.inventory_category.name if inventory_item.inventory_category_id and inventory_item.inventory_category else None
        ),
        "track_stock": inventory_item.track_stock,
        "track_lot": inventory_item.track_lot,
        "track_serial": inventory_item.track_serial,
        "track_expiry": inventory_item.track_expiry,
        "allow_negative_stock": inventory_item.allow_negative_stock,
        "minimum_stock_level": _decimal_to_float(inventory_item.minimum_stock_level),
        "reorder_point": _decimal_to_float(inventory_item.reorder_point),
        "reorder_quantity": _decimal_to_float(inventory_item.reorder_quantity),
        "status": resolved_summary.get("status") or inventory_item.status,
        "quantity": _decimal_to_float(resolved_summary.get("quantity", Decimal("0"))),
        "quantity_reserved": _decimal_to_float(resolved_summary.get("quantity_reserved", Decimal("0"))),
        "quantity_available": _decimal_to_float(resolved_summary.get("quantity_available", Decimal("0"))),
        "total_stock_value": _decimal_to_float(resolved_summary.get("total_stock_value", Decimal("0"))),
        "avg_purchase_price": _decimal_to_float(resolved_summary.get("avg_purchase_price", Decimal("0"))),
        "purchase_price": _decimal_to_float(resolved_summary.get("purchase_price", Decimal("0"))),
        "location_name": resolved_summary.get("location_name", ""),
        "location_count": resolved_summary.get("location_count", 0),
        "location_breakdown": _to_json_compatible(resolved_summary.get("location_breakdown", [])),
        "serial_count": resolved_summary.get("serial_count", 0),
        "lot_count": resolved_summary.get("lot_count", 0),
        "expiry_date": _to_json_compatible(resolved_summary.get("expiry_date")),
        "days_to_expiry": resolved_summary.get("days_to_expiry"),
        "last_movement_at": _to_json_compatible(resolved_summary.get("last_movement_at")),
        "product_variant": resolved_summary.get("product_variant")
        or inventory_item.barcode_snapshot
        or (str(inventory_item.product_variant_id) if inventory_item.product_variant_id else ""),
    }


def _location_payload(location: StockLocation, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_summary = summary or {}
    return {
        "id": str(location.id),
        "name": location.name,
        "code": location.code,
        "location_type": location.location_type.name if location.location_type_id and location.location_type else None,
        "parent_name": location.parent.name if location.parent_id and location.parent else None,
        "structural": location.structural,
        "external": location.external,
        "physical_address": location.physical_address or "",
        "description": location.description or "",
        "total_items": resolved_summary.get("total_items", 0),
        "total_quantity": _decimal_to_float(resolved_summary.get("total_quantity", Decimal("0"))),
        "total_value": _decimal_to_float(resolved_summary.get("total_value", Decimal("0"))),
        "expiring_soon_count": resolved_summary.get("expiring_soon_count", 0),
        "top_inventory_types": _to_json_compatible(resolved_summary.get("top_inventory_types", [])),
    }


def _reservation_payload(reservation: StockReservation) -> dict[str, Any]:
    return {
        "id": str(reservation.id),
        "inventory_item_id": str(reservation.inventory_item_id),
        "inventory_item_name": reservation.inventory_item.name_snapshot,
        "stock_location_id": str(reservation.stock_location_id),
        "stock_location_name": reservation.stock_location.name,
        "stock_lot_id": str(reservation.stock_lot_id) if reservation.stock_lot_id else None,
        "lot_number": reservation.stock_lot.lot_number if reservation.stock_lot_id and reservation.stock_lot else None,
        "stock_serial_id": str(reservation.stock_serial_id) if reservation.stock_serial_id else None,
        "serial_number": (
            reservation.stock_serial.serial_number if reservation.stock_serial_id and reservation.stock_serial else None
        ),
        "external_order_type": reservation.external_order_type,
        "external_order_id": reservation.external_order_id,
        "external_order_line_id": reservation.external_order_line_id,
        "reserved_quantity": _decimal_to_float(reservation.reserved_quantity),
        "fulfilled_quantity": _decimal_to_float(reservation.fulfilled_quantity),
        "remaining_quantity": _decimal_to_float(reservation.remaining_quantity),
        "status": reservation.status,
        "expires_at": _to_json_compatible(reservation.expires_at),
        "created_at": _to_json_compatible(reservation.created_at),
    }


def _movement_payload(movement: StockMovement) -> dict[str, Any]:
    return {
        "id": str(movement.id),
        "inventory_item_id": str(movement.inventory_item_id),
        "inventory_item_name": movement.inventory_item.name_snapshot,
        "movement_type": movement.movement_type,
        "quantity": _decimal_to_float(movement.quantity),
        "unit_cost": _decimal_to_float(movement.unit_cost),
        "from_location_id": str(movement.from_location_id) if movement.from_location_id else None,
        "from_location_name": movement.from_location.name if movement.from_location_id and movement.from_location else None,
        "to_location_id": str(movement.to_location_id) if movement.to_location_id else None,
        "to_location_name": movement.to_location.name if movement.to_location_id and movement.to_location else None,
        "stock_lot_id": str(movement.stock_lot_id) if movement.stock_lot_id else None,
        "lot_number": movement.stock_lot.lot_number if movement.stock_lot_id and movement.stock_lot else None,
        "stock_serial_id": str(movement.stock_serial_id) if movement.stock_serial_id else None,
        "serial_number": movement.stock_serial.serial_number if movement.stock_serial_id and movement.stock_serial else None,
        "reference_type": movement.reference_type,
        "reference_id": movement.reference_id,
        "actor_user_id": movement.actor_user_id,
        "occurred_at": _to_json_compatible(movement.occurred_at),
        "notes": movement.notes,
    }


def _inventory_queryset(*, principal: InventoryMcpPrincipal) -> QuerySet[Inventory]:
    return scope_queryset_by_identity(
        Inventory.objects.select_related("category", "default_supplier").order_by("-created_at", "name"),
        canonical_field="profile_id",
        legacy_field="profile",
        value=principal.profile_id,
    )


def _inventory_item_queryset(*, principal: InventoryMcpPrincipal) -> QuerySet[InventoryItem]:
    return scope_queryset_by_identity(
        InventoryItem.objects.select_related("inventory_category", "default_supplier").order_by("name_snapshot", "id"),
        canonical_field="profile_id",
        legacy_field="profile",
        value=principal.profile_id,
    )


def _stock_location_queryset(*, principal: InventoryMcpPrincipal) -> QuerySet[StockLocation]:
    return scope_queryset_by_identity(
        StockLocation.objects.select_related("location_type", "parent").order_by("name", "id"),
        canonical_field="profile_id",
        legacy_field="profile",
        value=principal.profile_id,
    )


def _stock_reservation_queryset(*, principal: InventoryMcpPrincipal) -> QuerySet[StockReservation]:
    return scope_queryset_by_identity(
        StockReservation.objects.select_related(
            "inventory_item",
            "stock_location",
            "stock_lot",
            "stock_serial",
        ).order_by("-created_at", "-id"),
        canonical_field="profile_id",
        legacy_field="profile",
        value=principal.profile_id,
    )


def _stock_movement_queryset(*, principal: InventoryMcpPrincipal) -> QuerySet[StockMovement]:
    return scope_queryset_by_identity(
        StockMovement.objects.select_related(
            "inventory_item",
            "from_location",
            "to_location",
            "stock_lot",
            "stock_serial",
        ).order_by("-occurred_at", "-created_at"),
        canonical_field="profile_id",
        legacy_field="profile",
        value=principal.profile_id,
    )


def _search_inventories_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str,
    limit: int,
    active_only: bool | None,
    inventory_type: str | None,
) -> dict[str, Any]:
    queryset = _inventory_queryset(principal=principal)
    if active_only is True:
        queryset = queryset.filter(active=True)
    elif active_only is False:
        queryset = queryset.filter(active=False)
    if inventory_type:
        queryset = queryset.filter(inventory_type=inventory_type)

    search_term = query.strip()
    queryset = queryset.filter(
        Q(name__icontains=search_term)
        | Q(description__icontains=search_term)
        | Q(external_system_id__icontains=search_term)
        | Q(category__name__icontains=search_term)
    ).distinct()
    inventories = list(queryset[:limit])
    summary_map = get_inventory_summary_map(inventories)
    return {
        "query": search_term,
        "count": len(inventories),
        "limit": limit,
        "profile_id": principal.profile_id,
        "company_code": principal.company_code,
        "results": [
            _inventory_payload(inventory, summary=summary_map.get(inventory.id, {}))
            for inventory in inventories
        ],
    }


def _get_inventory_details_sync(*, principal: InventoryMcpPrincipal, inventory_id: str) -> dict[str, Any]:
    inventory = _inventory_queryset(principal=principal).filter(id=inventory_id).first()
    if inventory is None:
        raise ValueError("Inventory not found.")
    summary = get_inventory_summary_map([inventory]).get(inventory.id, {})
    return {
        "profile_id": principal.profile_id,
        "company_code": principal.company_code,
        "inventory": _inventory_payload(inventory, summary=summary),
    }


def _search_stock_items_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    inventory_type: str | None,
    status: str | None,
    inventory_item_id: str | None,
) -> dict[str, Any]:
    queryset = _inventory_item_queryset(principal=principal)
    if inventory_type:
        queryset = queryset.filter(inventory_type=inventory_type)
    if status:
        queryset = queryset.filter(status=status)
    if inventory_item_id:
        queryset = queryset.filter(id=inventory_item_id)

    search_term = str(query or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(name_snapshot__icontains=search_term)
            | Q(sku_snapshot__icontains=search_term)
            | Q(barcode_snapshot__icontains=search_term)
            | Q(description__icontains=search_term)
        )

    items = list(queryset[:limit])
    summary_map = get_inventory_item_summary_map(items)
    return {
        "query": search_term or None,
        "count": len(items),
        "limit": limit,
        "profile_id": principal.profile_id,
        "results": [
            _inventory_item_payload(item, summary=summary_map.get(item.id, {}))
            for item in items
        ],
    }


def _get_inventory_item_details_sync(
    *,
    principal: InventoryMcpPrincipal,
    inventory_item_id: str,
    history_limit: int,
) -> dict[str, Any]:
    inventory_item = _inventory_item_queryset(principal=principal).filter(id=inventory_item_id).first()
    if inventory_item is None:
        raise ValueError("Inventory item not found.")
    summary = get_inventory_item_summary_map([inventory_item]).get(inventory_item.id, {})
    lots = list(
        scope_queryset_by_identity(
            StockLot.objects.filter(inventory_item_id=inventory_item.id).order_by("expiry_date", "-created_at"),
            canonical_field="profile_id",
            legacy_field="profile",
            value=principal.profile_id,
        )[:history_limit]
    )
    serials = list(
        scope_queryset_by_identity(
            StockSerial.objects.filter(inventory_item_id=inventory_item.id).order_by("serial_number"),
            canonical_field="profile_id",
            legacy_field="profile",
            value=principal.profile_id,
        )[:history_limit]
    )
    reservations = list(
        _stock_reservation_queryset(principal=principal).filter(inventory_item_id=inventory_item.id)[:history_limit]
    )
    movements = list(
        _stock_movement_queryset(principal=principal).filter(inventory_item_id=inventory_item.id)[:history_limit]
    )
    return {
        "profile_id": principal.profile_id,
        "inventory_item": _inventory_item_payload(inventory_item, summary=summary),
        "lots": [
            {
                "id": str(lot.id),
                "lot_number": lot.lot_number,
                "expiry_date": _to_json_compatible(lot.expiry_date),
                "unit_cost": _decimal_to_float(lot.unit_cost),
                "received_quantity": _decimal_to_float(lot.received_quantity),
                "remaining_quantity": _decimal_to_float(lot.remaining_quantity),
                "status": lot.status,
            }
            for lot in lots
        ],
        "serials": [
            {
                "id": str(serial.id),
                "serial_number": serial.serial_number,
                "status": serial.status,
                "stock_location_id": str(serial.stock_location_id) if serial.stock_location_id else None,
                "stock_location_name": serial.stock_location.name if serial.stock_location_id and serial.stock_location else None,
            }
            for serial in serials
        ],
        "active_reservations": [_reservation_payload(reservation) for reservation in reservations],
        "recent_movements": [_movement_payload(movement) for movement in movements],
    }


def _get_inventory_alerts_sync(
    *,
    principal: InventoryMcpPrincipal,
    limit: int,
    expiring_days: int,
) -> dict[str, Any]:
    inventories = list(_inventory_queryset(principal=principal).filter(active=True))
    summary_map = get_inventory_summary_map(inventories, expiring_days=expiring_days)
    low_stock = []
    needs_reorder = []
    out_of_stock = []
    expiring = []

    for inventory in inventories:
        summary = summary_map.get(inventory.id, {})
        current_stock = Decimal(summary.get("current_stock_level", Decimal("0")))
        payload = _inventory_payload(inventory, summary=summary)
        if current_stock <= 0:
            out_of_stock.append(payload)
        elif current_stock <= Decimal(inventory.minimum_stock_level):
            low_stock.append(payload)
        elif current_stock <= Decimal(inventory.re_order_point):
            needs_reorder.append(payload)
        if summary.get("expiring_soon_count", 0) > 0:
            expiring.append(payload)

    return {
        "profile_id": principal.profile_id,
        "expiring_days": expiring_days,
        "low_stock": low_stock[:limit],
        "needs_reorder": needs_reorder[:limit],
        "out_of_stock": out_of_stock[:limit],
        "expiring_soon": expiring[:limit],
    }


def _search_stock_locations_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    structural_only: bool | None,
    external_only: bool | None,
) -> dict[str, Any]:
    queryset = _stock_location_queryset(principal=principal)
    if structural_only is True:
        queryset = queryset.filter(structural=True)
    elif structural_only is False:
        queryset = queryset.filter(structural=False)
    if external_only is True:
        queryset = queryset.filter(external=True)
    elif external_only is False:
        queryset = queryset.filter(external=False)

    search_term = str(query or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(name__icontains=search_term)
            | Q(code__icontains=search_term)
            | Q(description__icontains=search_term)
            | Q(physical_address__icontains=search_term)
            | Q(location_type__name__icontains=search_term)
        )

    locations = list(queryset[:limit])
    return {
        "query": search_term or None,
        "count": len(locations),
        "limit": limit,
        "profile_id": principal.profile_id,
        "results": [
            _location_payload(location, summary=get_location_stock_summary(location))
            for location in locations
        ],
    }


def _get_stock_location_summary_sync(
    *,
    principal: InventoryMcpPrincipal,
    location_id: str,
) -> dict[str, Any]:
    location = _stock_location_queryset(principal=principal).filter(id=location_id).first()
    if location is None:
        raise ValueError("Stock location not found.")
    summary = get_location_stock_summary(location)
    return {
        "profile_id": principal.profile_id,
        "location": _location_payload(location, summary=summary),
    }


def _search_stock_reservations_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    status: str | None,
    external_order_type: str | None,
    inventory_item_id: str | None,
) -> dict[str, Any]:
    queryset = _stock_reservation_queryset(principal=principal)
    if status:
        queryset = queryset.filter(status=status)
    if external_order_type:
        queryset = queryset.filter(external_order_type=external_order_type)
    if inventory_item_id:
        queryset = queryset.filter(inventory_item_id=inventory_item_id)

    search_term = str(query or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(external_order_id__icontains=search_term)
            | Q(external_order_line_id__icontains=search_term)
            | Q(inventory_item__name_snapshot__icontains=search_term)
            | Q(stock_location__name__icontains=search_term)
            | Q(stock_lot__lot_number__icontains=search_term)
            | Q(stock_serial__serial_number__icontains=search_term)
        )

    reservations = list(queryset[:limit])
    return {
        "query": search_term or None,
        "count": len(reservations),
        "limit": limit,
        "profile_id": principal.profile_id,
        "results": [_reservation_payload(reservation) for reservation in reservations],
    }


def _search_stock_movements_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    movement_type: str | None,
    inventory_item_id: str | None,
    reference_id: str | None,
) -> dict[str, Any]:
    queryset = _stock_movement_queryset(principal=principal)
    if movement_type:
        queryset = queryset.filter(movement_type=movement_type)
    if inventory_item_id:
        queryset = queryset.filter(inventory_item_id=inventory_item_id)
    if reference_id:
        queryset = queryset.filter(reference_id=reference_id)

    search_term = str(query or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(inventory_item__name_snapshot__icontains=search_term)
            | Q(reference_type__icontains=search_term)
            | Q(reference_id__icontains=search_term)
            | Q(from_location__name__icontains=search_term)
            | Q(to_location__name__icontains=search_term)
            | Q(stock_lot__lot_number__icontains=search_term)
            | Q(stock_serial__serial_number__icontains=search_term)
            | Q(notes__icontains=search_term)
        )

    movements = list(queryset[:limit])
    return {
        "query": search_term or None,
        "count": len(movements),
        "limit": limit,
        "profile_id": principal.profile_id,
        "results": [_movement_payload(movement) for movement in movements],
    }


def _get_stock_analytics_sync(*, principal: InventoryMcpPrincipal) -> dict[str, Any]:
    analytics = get_profile_stock_analytics(profile_id=principal.profile_id)
    return {
        "profile_id": principal.profile_id,
        "company_code": principal.company_code,
        "analytics": _to_json_compatible(analytics),
    }


def _build_transport_security_settings() -> TransportSecuritySettings:
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_hosts.extend(_parse_csv(os.getenv("INVENTORY_MCP_ALLOWED_HOSTS") or os.getenv("ALLOWED_HOSTS")))

    allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    allowed_origins.extend(
        _parse_csv(os.getenv("INVENTORY_MCP_ALLOWED_ORIGINS") or os.getenv("CORS_ALLOWED_ORIGINS"))
    )

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(allowed_hosts)),
        allowed_origins=list(dict.fromkeys(allowed_origins)),
    )


MCP_SERVER_NAME = os.getenv("INVENTORY_MCP_SERVER_NAME") or "inventory-service-mcp"
MCP_SERVER_HOST = os.getenv("INVENTORY_MCP_HOST") or "0.0.0.0"
MCP_SERVER_PORT = int(os.getenv("INVENTORY_MCP_PORT") or "8000")
MCP_SERVER_LOG_LEVEL = (os.getenv("INVENTORY_MCP_LOG_LEVEL") or "info").upper()

mcp = FastMCP(
    MCP_SERVER_NAME,
    instructions=(
        "Tools for the Inventory service. Authenticated tools are scoped to the caller's profile_id "
        "from the forwarded User Service access token."
    ),
    host=MCP_SERVER_HOST,
    port=MCP_SERVER_PORT,
    log_level=MCP_SERVER_LOG_LEVEL,
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=_build_transport_security_settings(),
)


@mcp.tool(
    name="search_inventories",
    description="Search inventory ledgers for the authenticated workspace.",
)
async def search_inventories(
    query: str,
    limit: int = 10,
    active_only: bool | None = True,
    inventory_type: str | None = None,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    search_term = str(query or "").strip()
    if not search_term:
        raise ValueError("query is required")
    limit_value = max(1, min(int(limit), 25))
    return await sync_to_async(_search_inventories_sync, thread_sensitive=True)(
        principal=principal,
        query=search_term,
        limit=limit_value,
        active_only=active_only,
        inventory_type=inventory_type,
    )


@mcp.tool(
    name="get_inventory_details",
    description="Get detailed stock posture for a single inventory ledger.",
)
async def get_inventory_details(inventory_id: str) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    target_inventory_id = str(inventory_id or "").strip()
    if not target_inventory_id:
        raise ValueError("inventory_id is required")
    return await sync_to_async(_get_inventory_details_sync, thread_sensitive=True)(
        principal=principal,
        inventory_id=target_inventory_id,
    )


@mcp.tool(
    name="search_stock_items",
    description="Search inventory item records by name, SKU, barcode, or description.",
)
async def search_stock_items(
    query: str | None = None,
    limit: int = 10,
    inventory_type: str | None = None,
    status: str | None = None,
    inventory_item_id: str | None = None,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    return await sync_to_async(_search_stock_items_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        limit=limit_value,
        inventory_type=inventory_type,
        status=status,
        inventory_item_id=str(inventory_item_id).strip() if inventory_item_id else None,
    )


@mcp.tool(
    name="get_inventory_item_details",
    description="Get deep detail for an inventory item, including lots, serials, reservations, and recent movements.",
)
async def get_inventory_item_details(
    inventory_item_id: str,
    history_limit: int = 10,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    target_item_id = str(inventory_item_id or "").strip()
    if not target_item_id:
        raise ValueError("inventory_item_id is required")
    limit_value = max(1, min(int(history_limit), 25))
    return await sync_to_async(_get_inventory_item_details_sync, thread_sensitive=True)(
        principal=principal,
        inventory_item_id=target_item_id,
        history_limit=limit_value,
    )


@mcp.tool(
    name="get_inventory_alerts",
    description="Return low-stock, reorder, out-of-stock, and expiring inventory queues.",
)
async def get_inventory_alerts(limit: int = 10, expiring_days: int = 30) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    day_window = max(1, min(int(expiring_days), 365))
    return await sync_to_async(_get_inventory_alerts_sync, thread_sensitive=True)(
        principal=principal,
        limit=limit_value,
        expiring_days=day_window,
    )


@mcp.tool(
    name="search_stock_locations",
    description="Search stock locations, including summary stock posture for each location.",
)
async def search_stock_locations(
    query: str | None = None,
    limit: int = 10,
    structural_only: bool | None = None,
    external_only: bool | None = None,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    return await sync_to_async(_search_stock_locations_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        limit=limit_value,
        structural_only=structural_only,
        external_only=external_only,
    )


@mcp.tool(
    name="get_stock_location_summary",
    description="Get detailed quantity, value, and expiry posture for a stock location.",
)
async def get_stock_location_summary(location_id: str) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    target_location_id = str(location_id or "").strip()
    if not target_location_id:
        raise ValueError("location_id is required")
    return await sync_to_async(_get_stock_location_summary_sync, thread_sensitive=True)(
        principal=principal,
        location_id=target_location_id,
    )


@mcp.tool(
    name="search_stock_reservations",
    description="Search active or historical stock reservations by order reference, item, lot, serial, or location.",
)
async def search_stock_reservations(
    query: str | None = None,
    limit: int = 10,
    status: str | None = None,
    external_order_type: str | None = None,
    inventory_item_id: str | None = None,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    return await sync_to_async(_search_stock_reservations_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        limit=limit_value,
        status=status,
        external_order_type=external_order_type,
        inventory_item_id=str(inventory_item_id).strip() if inventory_item_id else None,
    )


@mcp.tool(
    name="search_stock_movements",
    description="Search stock movements by item, reference, movement type, lot, serial, or location.",
)
async def search_stock_movements(
    query: str | None = None,
    limit: int = 10,
    movement_type: str | None = None,
    inventory_item_id: str | None = None,
    reference_id: str | None = None,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    return await sync_to_async(_search_stock_movements_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        limit=limit_value,
        movement_type=movement_type,
        inventory_item_id=str(inventory_item_id).strip() if inventory_item_id else None,
        reference_id=str(reference_id).strip() if reference_id else None,
    )


@mcp.tool(
    name="get_stock_analytics",
    description="Get workspace-level stock analytics across locations, value, and aging posture.",
)
async def get_stock_analytics() -> dict[str, Any]:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_stock_analytics_sync, thread_sensitive=True)(principal=principal)


async def health(_: Any) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _build_app_lifespan(mcp_app: Starlette):
    @asynccontextmanager
    async def lifespan(_: Starlette):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    return lifespan


def create_app() -> Starlette:
    mount_path = (os.getenv("INVENTORY_MCP_MOUNT_PATH") or "/mcp").strip() or "/mcp"
    if not mount_path.startswith("/"):
        mount_path = f"/{mount_path}"
    mcp_app = mcp.streamable_http_app()
    return Starlette(
        debug=_parse_bool(os.getenv("INVENTORY_MCP_DEBUG"), default=False),
        lifespan=_build_app_lifespan(mcp_app),
        middleware=[Middleware(InventoryMcpAuthMiddleware)],
        routes=[
            Route("/health", endpoint=health),
            Mount(mount_path, app=mcp_app),
        ],
    )


app = create_app()


def main() -> None:
    uvicorn.run(
        "mcp_server.server:app",
        host=MCP_SERVER_HOST,
        port=MCP_SERVER_PORT,
        log_level=MCP_SERVER_LOG_LEVEL.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
