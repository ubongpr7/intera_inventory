from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django
from django.apps import apps
from asgiref.sync import sync_to_async

if not apps.ready:
    django.setup()

from django.db.models import Q, QuerySet
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import UntypedToken
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
import uvicorn

from mainapps.inventory.models import Inventory, InventoryItem
from mainapps.inventory.views import InventoryCategoryViewSet, InventoryViewSet
from mainapps.stock.models import StockLocation, StockMovement, StockReservation, StockSerial, StockLot
from mainapps.stock.views import StockItemViewSet, StockLocationViewSet, StockReservationViewSet
from subapps.services.inventory_read_model import (
    get_inventory_item_summary_map,
    get_inventory_summary_map,
    get_location_stock_summary,
    get_profile_stock_analytics,
)
from subapps.utils.request_context import coerce_identity_id, scope_queryset_by_identity
import uuid
from mainapps.inventory import payloads as inventory_payloads
from mainapps.stock import payloads as stock_payloads
from mainapps.orders import payloads as orders_payloads


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


def _payload_to_data(value: BaseModel | dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
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

def _get_all_inventory_sync(*, principal: InventoryMcpPrincipal) -> dict[str, Any]:
    queryset = _inventory_queryset(principal=principal)
    inventories = list(queryset)
    summary_map = get_inventory_summary_map(inventories)
    return {
        "profile_id": principal.profile_id, 
        "company_code": principal.company_code,
        "count": len(inventories),
        "results": [
            _inventory_payload(inventory, summary=summary_map.get(inventory.id, {}))
            for inventory in inventories
        ],
    }

def _list_inventory_items_sync(
    *,
    principal: InventoryMcpPrincipal,
    inventory_id: str | None = None,
) -> dict[str, Any]:
    queryset = _inventory_item_queryset(principal=principal)
    if inventory_id:
        inventory = _inventory_queryset(principal=principal).filter(id=inventory_id).first()
        if inventory is None:
            raise ValueError("Inventory not found.")
        queryset = queryset.filter(inventory_id=inventory_id)
    items = list(queryset)
    summary_map = get_inventory_item_summary_map(items) 
    return {
        "profile_id": principal.profile_id,
        "company_code": principal.company_code,
        "count": len(items),
        "results": [
            _inventory_item_payload(item, summary=summary_map.get(item.id, {}))
            for item in items
        ],
    }



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


def _invoke_view_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    viewset_cls,
    action: str,
    method: str,
    pk: str | None = None,
    data: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> Any:
    factory = APIRequestFactory()
    http_method = method.lower().strip()
    path = "/mcp/internal"
    sanitized_query_params = {
        key: value for key, value in (query_params or {}).items() if value not in (None, "")
    }
    if query_params:
        encoded_query = urlencode(
            sanitized_query_params,
            doseq=True,
        )
        if encoded_query:
            path = f"{path}?{encoded_query}"
    auth_header = f"Bearer {principal.token}"

    if http_method == "get":
        request = factory.get(path, data=sanitized_query_params, format="json", HTTP_AUTHORIZATION=auth_header)
    elif http_method == "post":
        request = factory.post(path, data=data or {}, format="json", HTTP_AUTHORIZATION=auth_header)
    elif http_method == "patch":
        request = factory.patch(path, data=data or {}, format="json", HTTP_AUTHORIZATION=auth_header)
    elif http_method == "put":
        request = factory.put(path, data=data or {}, format="json", HTTP_AUTHORIZATION=auth_header)
    elif http_method == "delete":
        request = factory.delete(path, data=data or {}, format="json", HTTP_AUTHORIZATION=auth_header)
    else:
        raise ValueError(f"Unsupported method: {method}")

    view = viewset_cls.as_view({http_method: action})
    response = view(request, pk=pk) if pk is not None else view(request)
    status_code = getattr(response, "status_code", 200)
    payload = getattr(response, "data", None)
    if payload is None:
        content = None
        if hasattr(response, "getvalue"):
            try:
                content = response.getvalue()
            except Exception:
                content = None
        if content is None and hasattr(response, "streaming_content"):
            try:
                content = b"".join(response.streaming_content)
            except Exception:
                content = None
        if content is None and hasattr(response, "content"):
            try:
                content = response.content
            except Exception:
                content = None
        if content is not None:
            filename = ""
            if hasattr(response, "headers"):
                disposition = response.headers.get("Content-Disposition", "")
                if "filename=" in disposition:
                    filename = disposition.split("filename=", 1)[1].strip('"')
            payload = {
                "content_type": getattr(response, "headers", {}).get("Content-Type", "application/octet-stream")
                if hasattr(response, "headers")
                else "application/octet-stream",
                "filename": filename or None,
                "size": len(content),
                "base64": base64.b64encode(content).decode("ascii"),
            }
    payload = _to_json_compatible(payload)
    if status_code >= 400:
        detail = payload if payload is not None else {"detail": "Request failed."}
        raise ValueError(str(detail))
    return payload


def _search_purchase_orders_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    status: str | None,
    limit: int,
) -> dict[str, Any]:
    from mainapps.orders.views import PurchaseOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=PurchaseOrderViewSet,
        action="list",
        method="get",
        query_params={
            "search": str(query or "").strip(),
            "status": status or "",
            "page_size": limit,
        },
    )
    return {
        "profile_id": principal.profile_id,
        "query": str(query or "").strip() or None,
        "status": status,
        "results": payload,
    }


def _get_purchase_order_details_sync(*, principal: InventoryMcpPrincipal, purchase_order_id: str) -> dict[str, Any]:
    from mainapps.orders.views import PurchaseOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=PurchaseOrderViewSet,
        action="retrieve",
        method="get",
        pk=purchase_order_id,
    )
    return {
        "profile_id": principal.profile_id,
        "purchase_order": payload,
    }


def _get_purchase_order_analytics_sync(*, principal: InventoryMcpPrincipal) -> dict[str, Any]:
    from mainapps.orders.views import PurchaseOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=PurchaseOrderViewSet,
        action="analytics",
        method="get",
    )
    return {
        "profile_id": principal.profile_id,
        "analytics": payload,
    }


def _purchase_order_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    purchase_order_id: str,
    action: str,
    method: str = "patch",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from mainapps.orders.views import PurchaseOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=PurchaseOrderViewSet,
        action=action,
        method=method,
        pk=purchase_order_id,
        data=data,
    )
    return {
        "profile_id": principal.profile_id,
        "purchase_order": payload,
    }


def _search_sales_orders_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    status: str | None,
    limit: int,
) -> dict[str, Any]:
    from mainapps.orders.views import SalesOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=SalesOrderViewSet,
        action="list",
        method="get",
        query_params={
            "search": str(query or "").strip(),
            "status": status or "",
            "page_size": limit,
        },
    )
    return {
        "profile_id": principal.profile_id,
        "query": str(query or "").strip() or None,
        "status": status,
        "results": payload,
    }


def _get_sales_order_details_sync(*, principal: InventoryMcpPrincipal, sales_order_id: str) -> dict[str, Any]:
    from mainapps.orders.views import SalesOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=SalesOrderViewSet,
        action="retrieve",
        method="get",
        pk=sales_order_id,
    )
    return {
        "profile_id": principal.profile_id,
        "sales_order": payload,
    }


def _sales_order_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    sales_order_id: str,
    action: str,
    method: str = "post",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from mainapps.orders.views import SalesOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=SalesOrderViewSet,
        action=action,
        method=method,
        pk=sales_order_id,
        data=data,
    )
    return {
        "profile_id": principal.profile_id,
        "sales_order": payload,
    }


def _search_return_orders_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    status: str | None,
    limit: int,
) -> dict[str, Any]:
    from mainapps.orders.views import ReturnOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=ReturnOrderViewSet,
        action="list",
        method="get",
        query_params={
            "search": str(query or "").strip(),
            "status": status or "",
            "page_size": limit,
        },
    )
    return {
        "profile_id": principal.profile_id,
        "query": str(query or "").strip() or None,
        "status": status,
        "results": payload,
    }


def _get_return_order_details_sync(*, principal: InventoryMcpPrincipal, return_order_id: str) -> dict[str, Any]:
    from mainapps.orders.views import ReturnOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=ReturnOrderViewSet,
        action="retrieve",
        method="get",
        pk=return_order_id,
    )
    return {
        "profile_id": principal.profile_id,
        "return_order": payload,
    }


def _return_order_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    return_order_id: str,
    action: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from mainapps.orders.views import ReturnOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=ReturnOrderViewSet,
        action=action,
        method="post",
        pk=return_order_id,
        data=data,
    )
    return {
        "profile_id": principal.profile_id,
        "return_order": payload,
    }


def _adjust_inventory_stock_via_view_sync(
    *,
    principal: InventoryMcpPrincipal,
    inventory_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=InventoryViewSet,
        action="adjust_stock",
        method="post",
        pk=inventory_id,
        data=data,
    )
    return {
        "profile_id": principal.profile_id,
        "inventory_adjustment": payload,
    }


def _transfer_stock_via_view_sync(
    *,
    principal: InventoryMcpPrincipal,
    location_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=StockLocationViewSet,
        action="transfer_stock",
        method="post",
        pk=location_id,
        data=data,
    )
    return {
        "profile_id": principal.profile_id,
        "stock_transfer": payload,
    }


def _create_stock_reservation_via_view_sync(
    *,
    principal: InventoryMcpPrincipal,
    data: dict[str, Any],
) -> dict[str, Any]:
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=StockReservationViewSet,
        action="create",
        method="post",
        data=data,
    )
    return {
        "profile_id": principal.profile_id,
        "reservation": payload,
    }


def _reservation_action_via_view_sync(
    *,
    principal: InventoryMcpPrincipal,
    reservation_id: str,
    action: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=StockReservationViewSet,
        action=action,
        method="post",
        pk=reservation_id,
        data=data,
    )
    return {
        "profile_id": principal.profile_id,
        "reservation": payload,
    }


def _inventory_category_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    action: str,
    method: str,
    category_id: str | None = None,
    data: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=InventoryCategoryViewSet,
        action=action,
        method=method,
        pk=category_id,
        data=data,
        query_params=query_params,
    )
    return {
        "profile_id": principal.profile_id,
        "category": payload,
    }


def _inventory_crud_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    action: str,
    method: str,
    inventory_id: str | None = None,
    data: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=InventoryViewSet,
        action=action,
        method=method,
        pk=inventory_id,
        data=data,
        query_params=query_params,
    )
    return {
        "profile_id": principal.profile_id,
        "inventory": payload,
    }


def _stock_location_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    action: str,
    method: str,
    location_id: str | None = None,
    data: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=StockLocationViewSet,
        action=action,
        method=method,
        pk=location_id,
        data=data,
        query_params=query_params,
    )
    return {
        "profile_id": principal.profile_id,
        "location": payload,
    }


def _stock_item_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    action: str,
    method: str,
    inventory_item_id: str | None = None,
    data: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=StockItemViewSet,
        action=action,
        method=method,
        pk=inventory_item_id,
        data=data,
        query_params=query_params,
    )
    return {
        "profile_id": principal.profile_id,
        "inventory_item": payload,
    }


def _purchase_order_line_item_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    purchase_order_id: str,
    action: str,
    method: str,
    data: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from mainapps.orders.views import PurchaseOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=PurchaseOrderViewSet,
        action=action,
        method=method,
        pk=purchase_order_id,
        data=data,
        query_params=query_params,
    )
    return {
        "profile_id": principal.profile_id,
        "purchase_order": payload,
    }


def _sales_order_line_item_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    sales_order_id: str,
    action: str,
    method: str,
    data: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from mainapps.orders.views import SalesOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=SalesOrderViewSet,
        action=action,
        method=method,
        pk=sales_order_id,
        data=data,
        query_params=query_params,
    )
    return {
        "profile_id": principal.profile_id,
        "sales_order": payload,
    }


def _purchase_order_admin_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    action: str,
    method: str,
    purchase_order_id: str | None = None,
    data: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from mainapps.orders.views import PurchaseOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=PurchaseOrderViewSet,
        action=action,
        method=method,
        pk=purchase_order_id,
        data=data,
        query_params=query_params,
    )
    return {
        "profile_id": principal.profile_id,
        "purchase_order": payload,
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

# _get_all_inventory_sync
@mcp.tool(
    name="get_all_inventory",
    description="Retrieve all inventory ledgers for the authenticated workspace.",
)
async def get_all_inventory() -> inventory_payloads.InventoryCollectionResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_all_inventory_sync, thread_sensitive=True)(
        principal=principal
    )
# _list_inventory_items_sync
@mcp.tool(
    name="list_inventory_items",
    description="Retrieve all inventory items for the authenticated workspace.",
)
async def list_inventory_items(
    inventory_id: str | None = None,
) -> inventory_payloads.InventoryItemCollectionResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_list_inventory_items_sync, thread_sensitive=True)(
        principal=principal,
        inventory_id=str(inventory_id).strip() if inventory_id else None,
    )


# _search_inventories_sync

@mcp.tool(
    name="search_inventories",
    description="Search inventory ledgers for the authenticated workspace.",
)
async def search_inventories(
    query: str,
    limit: int = 10,
    active_only: bool | None = True,
    inventory_type: str | None = None,
) -> inventory_payloads.InventoryCollectionResponsePayload:
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
async def get_inventory_details(
    inventory_id: str,
) -> inventory_payloads.InventoryDetailResponsePayload:
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
) -> inventory_payloads.InventoryItemCollectionResponsePayload:
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
) -> stock_payloads.InventoryItemDetailResponsePayload:
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
async def get_inventory_alerts(
    limit: int = 10,
    expiring_days: int = 30,
) -> inventory_payloads.InventoryAlertsResponsePayload:
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
) -> stock_payloads.StockLocationCollectionResponsePayload:
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
async def get_stock_location_summary(
    location_id: str,
) -> stock_payloads.StockLocationSummaryResponsePayload:
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
) -> stock_payloads.StockReservationCollectionResponsePayload:
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
) -> stock_payloads.StockMovementCollectionResponsePayload:
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
async def get_stock_analytics() -> inventory_payloads.InventoryAnalyticsResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_stock_analytics_sync, thread_sensitive=True)(principal=principal)


@mcp.tool(
    name="search_purchase_orders",
    description="Search purchase orders for the authenticated workspace by reference, supplier, or status.",
)
async def search_purchase_orders(
    query: str | None = None,
    status: str | None = None,
    limit: int = 10,
) -> orders_payloads.PurchaseOrderSearchResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 50))
    return await sync_to_async(_search_purchase_orders_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        status=status,
        limit=limit_value,
    )


@mcp.tool(
    name="get_purchase_order_details",
    description="Get a single purchase order with the backend's canonical detail payload.",
)
async def get_purchase_order_details(
    purchase_order_id: str,
) -> orders_payloads.PurchaseOrderDetailResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    return await sync_to_async(_get_purchase_order_details_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
    )


@mcp.tool(
    name="get_purchase_order_analytics",
    description="Get purchase-order analytics for the authenticated workspace.",
)
async def get_purchase_order_analytics() -> orders_payloads.PurchaseOrderAnalyticsResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_purchase_order_analytics_sync, thread_sensitive=True)(
        principal=principal,
    )


@mcp.tool(
    name="approve_purchase_order",
    description="Approve a purchase order. Optional payload may include notes or approval metadata expected by the backend.",
)
async def approve_purchase_order(
    purchase_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.PurchaseOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    return await sync_to_async(_purchase_order_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
        action="approve",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="issue_purchase_order",
    description="Issue a purchase order to the supplier. Payload may include notes or workflow data required by the backend.",
)
async def issue_purchase_order(
    purchase_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.PurchaseOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    return await sync_to_async(_purchase_order_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
        action="issue",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="receive_purchase_order_items",
    description="Receive specific items on a purchase order. Payload should match the backend receive_items action schema.",
)
async def receive_purchase_order_items(
    purchase_order_id: str,
    payload: orders_payloads.PurchaseOrderReceiveItemsPayload,
) -> orders_payloads.PurchaseOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_purchase_order_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
        action="receive_items",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="complete_purchase_order",
    description="Mark a purchase order as complete.",
)
async def complete_purchase_order(
    purchase_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.PurchaseOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    return await sync_to_async(_purchase_order_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
        action="complete",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="cancel_purchase_order",
    description="Cancel a purchase order. Payload can include notes or a cancellation reason.",
)
async def cancel_purchase_order(
    purchase_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.PurchaseOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    return await sync_to_async(_purchase_order_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
        action="cancel",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="create_purchase_return_order",
    description="Create a return order from a purchase order. Payload can include reason, items, and notes expected by the backend.",
)
async def create_purchase_return_order(
    purchase_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.PurchaseOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    return await sync_to_async(_purchase_order_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
        action="create_return_order",
        method="post",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="search_sales_orders",
    description="Search sales orders for the authenticated workspace by reference, customer, or status.",
)
async def search_sales_orders(
    query: str | None = None,
    status: str | None = None,
    limit: int = 10,
) -> orders_payloads.SalesOrderSearchResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 50))
    return await sync_to_async(_search_sales_orders_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        status=status,
        limit=limit_value,
    )


@mcp.tool(
    name="get_sales_order_details",
    description="Get a single sales order with the backend's canonical detail payload.",
)
async def get_sales_order_details(
    sales_order_id: str,
) -> orders_payloads.SalesOrderDetailResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    return await sync_to_async(_get_sales_order_details_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
    )


@mcp.tool(
    name="reserve_sales_order",
    description="Request stock reservation for a sales order. Payload should match the backend reserve action schema.",
)
async def reserve_sales_order(
    sales_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.SalesOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    return await sync_to_async(_sales_order_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
        action="reserve",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="release_sales_order",
    description="Release stock reservation for a sales order.",
)
async def release_sales_order(
    sales_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.SalesOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    return await sync_to_async(_sales_order_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
        action="release",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="ship_sales_order",
    description="Ship a sales order. Payload should match the backend ship action schema.",
)
async def ship_sales_order(
    sales_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.SalesOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    return await sync_to_async(_sales_order_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
        action="ship",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="complete_sales_order",
    description="Mark a sales order as complete.",
)
async def complete_sales_order(
    sales_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.SalesOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    return await sync_to_async(_sales_order_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
        action="complete",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="cancel_sales_order",
    description="Cancel a sales order. Payload can include notes or a cancellation reason.",
)
async def cancel_sales_order(
    sales_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.SalesOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    return await sync_to_async(_sales_order_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
        action="cancel",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="search_return_orders",
    description="Search return orders for the authenticated workspace.",
)
async def search_return_orders(
    query: str | None = None,
    status: str | None = None,
    limit: int = 10,
) -> orders_payloads.ReturnOrderSearchResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 50))
    return await sync_to_async(_search_return_orders_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        status=status,
        limit=limit_value,
    )


@mcp.tool(
    name="get_return_order_details",
    description="Get a single return order with the backend's canonical detail payload.",
)
async def get_return_order_details(
    return_order_id: str,
) -> orders_payloads.ReturnOrderDetailResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(return_order_id or "").strip()
    if not target_id:
        raise ValueError("return_order_id is required")
    return await sync_to_async(_get_return_order_details_sync, thread_sensitive=True)(
        principal=principal,
        return_order_id=target_id,
    )


@mcp.tool(
    name="dispatch_return_order",
    description="Dispatch a return order.",
)
async def dispatch_return_order(
    return_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.ReturnOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(return_order_id or "").strip()
    if not target_id:
        raise ValueError("return_order_id is required")
    return await sync_to_async(_return_order_action_sync, thread_sensitive=True)(
        principal=principal,
        return_order_id=target_id,
        action="dispatch",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="complete_return_order",
    description="Complete a return order.",
)
async def complete_return_order(
    return_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.ReturnOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(return_order_id or "").strip()
    if not target_id:
        raise ValueError("return_order_id is required")
    return await sync_to_async(_return_order_action_sync, thread_sensitive=True)(
        principal=principal,
        return_order_id=target_id,
        action="complete",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="cancel_return_order",
    description="Cancel a return order. Payload can include notes or a cancellation reason.",
)
async def cancel_return_order(
    return_order_id: str,
    payload: orders_payloads.OrderActionPayload | None = None,
) -> orders_payloads.ReturnOrderActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(return_order_id or "").strip()
    if not target_id:
        raise ValueError("return_order_id is required")
    return await sync_to_async(_return_order_action_sync, thread_sensitive=True)(
        principal=principal,
        return_order_id=target_id,
        action="cancel",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="adjust_inventory_stock",
    description="Adjust stock on an inventory ledger. Payload should match the backend adjust_stock action schema.",
)
async def adjust_inventory_stock(
    inventory_id: str,
    payload: stock_payloads.InventoryAdjustmentRequestPayload,
) -> stock_payloads.StockAdjustmentResultPayload:
    principal = get_current_principal(required=True)
    target_id = str(inventory_id or "").strip()
    if not target_id:
        raise ValueError("inventory_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_adjust_inventory_stock_via_view_sync, thread_sensitive=True)(
        principal=principal,
        inventory_id=target_id,
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="transfer_location_stock",
    description="Transfer stock from one location to another. Payload should match the backend transfer_stock action schema.",
)
async def transfer_location_stock(
    location_id: str,
    payload: stock_payloads.StockTransferRequestPayload,
) -> stock_payloads.StockTransferResultPayload:
    principal = get_current_principal(required=True)
    target_id = str(location_id or "").strip()
    if not target_id:
        raise ValueError("location_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_transfer_stock_via_view_sync, thread_sensitive=True)(
        principal=principal,
        location_id=target_id,
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="create_stock_reservation",
    description="Create a stock reservation. Payload should match the backend reservation create schema.",
)
async def create_stock_reservation(
    payload: stock_payloads.StockReservationCreateUpdatePayload,
) -> stock_payloads.StockReservationMutationResponsePayload:
    principal = get_current_principal(required=True)
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_create_stock_reservation_via_view_sync, thread_sensitive=True)(
        principal=principal,
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="release_stock_reservation",
    description="Release a stock reservation. Payload can include notes or quantities required by the backend.",
)
async def release_stock_reservation(
    reservation_id: str,
    payload: stock_payloads.StockReservationActionPayload | None = None,
) -> stock_payloads.StockReservationMutationResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(reservation_id or "").strip()
    if not target_id:
        raise ValueError("reservation_id is required")
    return await sync_to_async(_reservation_action_via_view_sync, thread_sensitive=True)(
        principal=principal,
        reservation_id=target_id,
        action="release",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="fulfill_stock_reservation",
    description="Fulfill a stock reservation. Payload can include notes or quantities required by the backend.",
)
async def fulfill_stock_reservation(
    reservation_id: str,
    payload: stock_payloads.StockReservationActionPayload | None = None,
) -> stock_payloads.StockReservationMutationResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(reservation_id or "").strip()
    if not target_id:
        raise ValueError("reservation_id is required")
    return await sync_to_async(_reservation_action_via_view_sync, thread_sensitive=True)(
        principal=principal,
        reservation_id=target_id,
        action="fulfill",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="list_inventory_categories",
    description="List inventory categories for the authenticated workspace.",
)
async def list_inventory_categories(
    query: str | None = None,
    limit: int = 25,
    active_only: bool | None = None,
) -> inventory_payloads.InventoryCategoryCollectionResponsePayload:
    principal = get_current_principal(required=True)
    payload = await sync_to_async(_inventory_category_action_sync, thread_sensitive=True)(
        principal=principal,
        action="list",
        method="get",
        query_params={
            "search": query,
            "is_active": active_only,
            "page_size": max(1, min(int(limit), 50)),
        },
    )
    return payload


@mcp.tool(
    name="get_inventory_category_tree",
    description="Get the hierarchical tree of inventory categories.",
)
async def get_inventory_category_tree() -> inventory_payloads.InventoryCategoryCollectionResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_inventory_category_action_sync, thread_sensitive=True)(
        principal=principal,
        action="tree",
        method="get",
    )


@mcp.tool(
    name="get_inventory_category_details",
    description="Get a single inventory category in the backend's canonical detail payload.",
)
async def get_inventory_category_details(
    category_id: str,
) -> inventory_payloads.InventoryCategoryDetailResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(category_id or "").strip()
    if not target_id:
        raise ValueError("category_id is required")
    return await sync_to_async(_inventory_category_action_sync, thread_sensitive=True)(
        principal=principal,
        action="retrieve",
        method="get",
        category_id=target_id,
    )


@mcp.tool(
    name="get_inventory_category_children",
    description="Get direct child categories for an inventory category.",
)
async def get_inventory_category_children(
    category_id: str,
) -> inventory_payloads.InventoryCategoryCollectionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(category_id or "").strip()
    if not target_id:
        raise ValueError("category_id is required")
    return await sync_to_async(_inventory_category_action_sync, thread_sensitive=True)(
        principal=principal,
        action="children",
        method="get",
        category_id=target_id,
    )


@mcp.tool(
    name="get_inventory_category_inventories",
    description="Get inventories attached to an inventory category.",
)
async def get_inventory_category_inventories(
    category_id: str,
) -> inventory_payloads.InventoryCategoryCollectionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(category_id or "").strip()
    if not target_id:
        raise ValueError("category_id is required")
    return await sync_to_async(_inventory_category_action_sync, thread_sensitive=True)(
        principal=principal,
        action="inventories",
        method="get",
        category_id=target_id,
    )


@mcp.tool(
    name="create_inventory_category",
    description="Create an inventory category. Payload should match the backend create schema.",
)
async def create_inventory_category(
    payload: inventory_payloads.InventoryCategoryCreateUpdatePayload,
) -> inventory_payloads.InventoryCategoryMutationResponsePayload:
    principal = get_current_principal(required=True)
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_inventory_category_action_sync, thread_sensitive=True)(
        principal=principal,
        action="create",
        method="post",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="update_inventory_category",
    description="Update an inventory category. Payload should match the backend partial-update schema.",
)
async def update_inventory_category(
    category_id: str,
    payload: inventory_payloads.InventoryCategoryCreateUpdatePayload,
) -> inventory_payloads.InventoryCategoryMutationResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(category_id or "").strip()
    if not target_id:
        raise ValueError("category_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_inventory_category_action_sync, thread_sensitive=True)(
        principal=principal,
        action="partial_update",
        method="patch",
        category_id=target_id,
        data=_payload_to_data(payload),
    )

@mcp.tool(
    name="create_inventory",
    description="Create an inventory ledger/item definition. Payload should match the backend create schema.",
)
async def create_inventory(
    payload: inventory_payloads.InventoryCreateUpdatePayload,
) -> inventory_payloads.InventoryMutationResponsePayload:
    #  we need to properly define all payload fields and validation for this tool before we can safely expose it, as it has significant potential to cause data integrity issues if used incorrectly. For now, we'll leave this as a passthrough to the view action and require internal access until we can build out a more robust interface for inventory creation.
    principal = get_current_principal(required=True)
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_inventory_crud_action_sync, thread_sensitive=True)(
        principal=principal,
        action="create",
        method="post",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="update_inventory",
    description="Update an inventory ledger/item definition. Payload should match the backend partial-update schema.",
)
async def update_inventory(
    inventory_id: str,
    payload: inventory_payloads.InventoryCreateUpdatePayload,
) -> inventory_payloads.InventoryMutationResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(inventory_id or "").strip()
    if not target_id:
        raise ValueError("inventory_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_inventory_crud_action_sync, thread_sensitive=True)(
        principal=principal,
        action="partial_update",
        method="patch",
        inventory_id=target_id,
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="create_stock_location",
    description="Create a stock location. Payload should match the backend create schema.",
)
async def create_stock_location(
    payload: stock_payloads.StockLocationCreateUpdatePayload,
) -> stock_payloads.StockLocationMutationResponsePayload:
    principal = get_current_principal(required=True)
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_stock_location_action_sync, thread_sensitive=True)(
        principal=principal,
        action="create",
        method="post",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="update_stock_location",
    description="Update a stock location. Payload should match the backend partial-update schema.",
)
async def update_stock_location(
    location_id: str,
    payload: stock_payloads.StockLocationCreateUpdatePayload,
) -> stock_payloads.StockLocationMutationResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(location_id or "").strip()
    if not target_id:
        raise ValueError("location_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_stock_location_action_sync, thread_sensitive=True)(
        principal=principal,
        action="partial_update",
        method="patch",
        location_id=target_id,
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="get_stock_item_tracking_history",
    description="Get the full movement history for an inventory stock item.",
)
async def get_stock_item_tracking_history(
    inventory_item_id: str,
) -> stock_payloads.StockItemActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(inventory_item_id or "").strip()
    if not target_id:
        raise ValueError("inventory_item_id is required")
    return await sync_to_async(_stock_item_action_sync, thread_sensitive=True)(
        principal=principal,
        action="tracking_history",
        method="get",
        inventory_item_id=target_id,
    )


@mcp.tool(
    name="update_stock_item_status",
    description="Update the lifecycle status of an inventory stock item.",
)
async def update_stock_item_status(
    inventory_item_id: str,
    payload: stock_payloads.StockStatusUpdatePayload,
) -> stock_payloads.StockItemActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(inventory_item_id or "").strip()
    if not target_id:
        raise ValueError("inventory_item_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_stock_item_action_sync, thread_sensitive=True)(
        principal=principal,
        action="update_status",
        method="post",
        inventory_item_id=target_id,
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="search_expiring_stock_items",
    description="List stock items that are expiring soon.",
)
async def search_expiring_stock_items(
    days: int = 30,
) -> stock_payloads.StockItemActionResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_stock_item_action_sync, thread_sensitive=True)(
        principal=principal,
        action="expiring_soon",
        method="get",
        query_params={"days": max(1, min(int(days), 365))},
    )


@mcp.tool(
    name="search_low_stock_items",
    description="List low-stock inventory items from the stock service dashboard view.",
)
async def search_low_stock_items() -> stock_payloads.StockItemActionResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_stock_item_action_sync, thread_sensitive=True)(
        principal=principal,
        action="low_stock",
        method="get",
    )


@mcp.tool(
    name="list_purchase_order_line_items",
    description="List line items for a purchase order.",
)
async def list_purchase_order_line_items(
    purchase_order_id: str,
) -> orders_payloads.PurchaseOrderLineItemsResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    return await sync_to_async(_purchase_order_line_item_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
        action="line_items",
        method="get",
    )


@mcp.tool(
    name="add_purchase_order_line_item",
    description="Add a line item to a purchase order.",
)
async def add_purchase_order_line_item(
    purchase_order_id: str,
    payload: orders_payloads.PurchaseOrderLineItemActionPayload,
) -> orders_payloads.PurchaseOrderLineItemsResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_purchase_order_line_item_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
        action="add_line_item",
        method="post",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="update_purchase_order_line_item",
    description="Update an existing purchase-order line item.",
)
async def update_purchase_order_line_item(
    purchase_order_id: str,
    payload: orders_payloads.PurchaseOrderLineItemActionPayload,
) -> orders_payloads.PurchaseOrderLineItemsResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_purchase_order_line_item_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_id,
        action="update_line_item",
        method="patch",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="remove_purchase_order_line_item",
    description="Remove a line item from a purchase order.",
)
async def remove_purchase_order_line_item(
    purchase_order_id: str,
    line_item_id: str,
) -> orders_payloads.PurchaseOrderLineItemsResponsePayload:
    principal = get_current_principal(required=True)
    target_order_id = str(purchase_order_id or "").strip()
    target_line_item_id = str(line_item_id or "").strip()
    if not target_order_id:
        raise ValueError("purchase_order_id is required")
    if not target_line_item_id:
        raise ValueError("line_item_id is required")
    return await sync_to_async(_purchase_order_line_item_action_sync, thread_sensitive=True)(
        principal=principal,
        purchase_order_id=target_order_id,
        action="remove_line_item",
        method="delete",
        query_params={"line_item_id": target_line_item_id},
    )


@mcp.tool(
    name="download_purchase_order_pdf",
    description="Download a purchase order PDF. Returns filename, content type, size, and base64 payload.",
)
async def download_purchase_order_pdf(
    purchase_order_id: str,
) -> orders_payloads.PurchaseOrderAdminResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    return await sync_to_async(_purchase_order_admin_action_sync, thread_sensitive=True)(
        principal=principal,
        action="download_pdf",
        method="get",
        purchase_order_id=target_id,
    )


@mcp.tool(
    name="resend_purchase_order_email",
    description="Resend a purchase-order email to the supplier.",
)
async def resend_purchase_order_email(
    purchase_order_id: str,
) -> orders_payloads.PurchaseOrderAdminResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(purchase_order_id or "").strip()
    if not target_id:
        raise ValueError("purchase_order_id is required")
    return await sync_to_async(_purchase_order_admin_action_sync, thread_sensitive=True)(
        principal=principal,
        action="resend_email",
        method="post",
        data={"order_id": target_id},
    )


@mcp.tool(
    name="list_sales_order_line_items",
    description="List line items for a sales order.",
)
async def list_sales_order_line_items(
    sales_order_id: str,
) -> orders_payloads.SalesOrderLineItemsResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    return await sync_to_async(_sales_order_line_item_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
        action="line_items",
        method="get",
    )


@mcp.tool(
    name="get_sales_order_shipments",
    description="Get shipments for a sales order.",
)
async def get_sales_order_shipments(
    sales_order_id: str,
) -> orders_payloads.SalesOrderLineItemsResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    return await sync_to_async(_sales_order_line_item_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
        action="shipments",
        method="get",
    )


@mcp.tool(
    name="add_sales_order_line_item",
    description="Add a line item to a sales order.",
)
async def add_sales_order_line_item(
    sales_order_id: str,
    payload: orders_payloads.SalesOrderLineItemActionPayload,
) -> orders_payloads.SalesOrderLineItemsResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_sales_order_line_item_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
        action="add_line_item",
        method="post",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="update_sales_order_line_item",
    description="Update an existing sales-order line item.",
)
async def update_sales_order_line_item(
    sales_order_id: str,
    payload: orders_payloads.SalesOrderLineItemActionPayload,
) -> orders_payloads.SalesOrderLineItemsResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(sales_order_id or "").strip()
    if not target_id:
        raise ValueError("sales_order_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_sales_order_line_item_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_id,
        action="update_line_item",
        method="patch",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="remove_sales_order_line_item",
    description="Remove a line item from a sales order.",
)
async def remove_sales_order_line_item(
    sales_order_id: str,
    line_item_id: str,
) -> orders_payloads.SalesOrderLineItemsResponsePayload:
    principal = get_current_principal(required=True)
    target_order_id = str(sales_order_id or "").strip()
    target_line_item_id = str(line_item_id or "").strip()
    if not target_order_id:
        raise ValueError("sales_order_id is required")
    if not target_line_item_id:
        raise ValueError("line_item_id is required")
    return await sync_to_async(_sales_order_line_item_action_sync, thread_sensitive=True)(
        principal=principal,
        sales_order_id=target_order_id,
        action="remove_line_item",
        method="delete",
        query_params={"line_item_id": target_line_item_id},
    )


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
