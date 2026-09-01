from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from pydantic import BaseModel

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django
from django.apps import apps
from django.db import close_old_connections
from asgiref.sync import sync_to_async

if not apps.ready:
    django.setup()

from django.db.models import Count, Q, QuerySet, Sum
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

from mainapps.inventory.models import InventoryCategory, InventoryItem
from mainapps.orders.models import PurchaseOrder, PurchaseOrderStatus
from mainapps.inventory.views import (
    InventoryCategoryViewSet,
    InventoryItemViewSet as InventoryCatalogItemViewSet,
)
from mainapps.stock.models import (
    StockBalance,
    StockLocation,
    StockLocationType,
    StockLot,
    StockMovement,
    StockReservation,
    StockSerial,
)
from mainapps.stock.views import (
    InventoryItemViewSet as InventoryOperationsItemViewSet,
    StockLocationViewSet,
    StockReservationViewSet,
)
from subapps.services.inventory_read_model import (
    get_inventory_item_summary_map,
    get_location_stock_summary,
    get_profile_stock_analytics,
)
from subapps.services.location_scope import (
    get_location_scope_ids_for_locations,
    resolve_structural_locations,
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
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_json_compatible(item) for item in value]
    return value


def _optional_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _purchase_order_target_date(order: Any) -> str | None:
    for field_name in ("target_date", "expected_delivery_date", "delivery_date", "due_date"):
        value = getattr(order, field_name, None)
        if value:
            return _optional_iso(value)
    return None


def _payload_to_data(value: BaseModel | dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value


def _prepare_inventory_item_payload_data(data: dict[str, Any] | None) -> dict[str, Any]:
    prepared = dict(data or {})
    # MCP contracts use explicit *_id names, while DRF ModelSerializer expects
    # the FK field names for writes. Normalize here so tool calls persist.
    if "inventory_category_id" in prepared and "inventory_category" not in prepared:
        prepared["inventory_category"] = prepared.pop("inventory_category_id")
    if "default_supplier_id" in prepared and "default_supplier" not in prepared:
        prepared["default_supplier"] = prepared.pop("default_supplier_id")
    return prepared


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


def _merge_authorization_context(claims: dict[str, Any], context_token: str | None) -> dict[str, Any]:
    if not context_token:
        return claims
    context = dict(UntypedToken(context_token).payload)
    if (
        context.get("token_type") != "intera_authorization_context"
        or str(context.get("user_id") or "") != str(claims.get("user_id") or claims.get("sub") or "")
        or str(context.get("profile_id") or "") != str(claims.get("profile_id") or "")
    ):
        raise RuntimeError("Authorization context does not match the access token.")
    permissions = set(claims.get("permissions") or []) | set(context.get("permissions") or [])
    for wildcard in context.get("wildcards") or []:
        permissions.update((context.get("wildcard_permissions") or {}).get(wildcard) or [])
    return {**claims, "permissions": sorted(str(item) for item in permissions if str(item).strip())}


def _build_principal_from_token(token: str, context_token: str | None = None) -> InventoryMcpPrincipal:
    claims = dict(UntypedToken(token).payload)
    claims = _merge_authorization_context(claims, context_token)
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
            principal = _build_principal_from_token(token, headers.get("x-intera-authorization-context"))
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
        "product_variant_image_url": inventory_item.product_variant_image_url or "",
    }


def _location_payload(location: StockLocation, *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_summary = summary or {}
    return {
        "id": str(location.id),
        "name": location.name,
        "code": location.code,
        "location_type_id": str(location.location_type_id) if location.location_type_id else None,
        "location_type": location.location_type.name if location.location_type_id and location.location_type else None,
        "parent_id": str(location.parent_id) if location.parent_id else None,
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


def _location_type_payload(location_type: StockLocationType) -> dict[str, Any]:
    return {
        "id": str(location_type.id),
        "name": location_type.name,
        "description": location_type.description or "",
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


def _lot_payload(lot: StockLot) -> dict[str, Any]:
    return {
        "id": str(lot.id),
        "inventory_item_id": str(lot.inventory_item_id),
        "inventory_item_name": lot.inventory_item.name_snapshot,
        "lot_number": lot.lot_number,
        "expiry_date": _to_json_compatible(lot.expiry_date),
        "unit_cost": _decimal_to_float(lot.unit_cost),
        "received_quantity": _decimal_to_float(lot.received_quantity),
        "remaining_quantity": _decimal_to_float(lot.remaining_quantity),
        "status": lot.status,
    }


def _serial_payload(serial: StockSerial) -> dict[str, Any]:
    return {
        "id": str(serial.id),
        "inventory_item_id": str(serial.inventory_item_id),
        "inventory_item_name": serial.inventory_item.name_snapshot,
        "stock_lot_id": str(serial.stock_lot_id) if serial.stock_lot_id else None,
        "lot_number": serial.stock_lot.lot_number if serial.stock_lot_id and serial.stock_lot else None,
        "serial_number": serial.serial_number,
        "status": serial.status,
        "stock_location_id": str(serial.stock_location_id) if serial.stock_location_id else None,
        "stock_location_name": serial.stock_location.name if serial.stock_location_id and serial.stock_location else None,
    }


def _balance_payload(balance: StockBalance) -> dict[str, Any]:
    return {
        "id": str(balance.id),
        "inventory_item_id": str(balance.inventory_item_id),
        "inventory_item_name": balance.inventory_item.name_snapshot,
        "stock_location_id": str(balance.stock_location_id),
        "stock_location_name": balance.stock_location.name,
        "stock_lot_id": str(balance.stock_lot_id) if balance.stock_lot_id else None,
        "lot_number": balance.stock_lot.lot_number if balance.stock_lot_id and balance.stock_lot else None,
        "quantity_on_hand": _decimal_to_float(balance.quantity_on_hand),
        "quantity_reserved": _decimal_to_float(balance.quantity_reserved),
        "quantity_available": _decimal_to_float(balance.quantity_available),
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


def _inventory_queryset(*, principal: InventoryMcpPrincipal) -> QuerySet[InventoryItem]:
    return scope_queryset_by_identity(
        InventoryItem.objects.select_related("inventory_category", "default_supplier").order_by("-created_at", "name_snapshot"),
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


def _resolve_structural_scope_locations_sync(
    *,
    principal: InventoryMcpPrincipal,
    structural_location_id: str | None,
) -> list[StockLocation]:
    normalized_id = str(structural_location_id or "").strip()
    if not normalized_id:
        return []

    location = _stock_location_queryset(principal=principal).filter(id=normalized_id).first()
    if location is None:
        raise ValueError("Structural location not found.")

    resolved_locations = resolve_structural_locations(
        profile_id=principal.profile_id,
        stock_locations=[location],
    )
    if not resolved_locations:
        raise ValueError("Structural location not found.")
    return resolved_locations


def _resolve_scoped_leaf_location_ids_sync(
    *,
    principal: InventoryMcpPrincipal,
    structural_location_id: str | None,
) -> list[uuid.UUID] | None:
    scoped_locations = _resolve_structural_scope_locations_sync(
        principal=principal,
        structural_location_id=structural_location_id,
    )
    if not scoped_locations:
        return None
    scoped_ids = get_location_scope_ids_for_locations(
        profile_id=principal.profile_id,
        stock_locations=scoped_locations,
    )
    return scoped_ids or None


def _stock_location_type_queryset() -> QuerySet[StockLocationType]:
    return StockLocationType.objects.order_by("name", "id")


def _stock_lot_queryset(*, principal: InventoryMcpPrincipal) -> QuerySet[StockLot]:
    return scope_queryset_by_identity(
        StockLot.objects.select_related("inventory_item").order_by("expiry_date", "-created_at"),
        canonical_field="profile_id",
        legacy_field="profile",
        value=principal.profile_id,
    )


def _stock_serial_queryset(*, principal: InventoryMcpPrincipal) -> QuerySet[StockSerial]:
    return scope_queryset_by_identity(
        StockSerial.objects.select_related("inventory_item", "stock_location", "stock_lot").order_by("serial_number", "id"),
        canonical_field="profile_id",
        legacy_field="profile",
        value=principal.profile_id,
    )


def _stock_balance_queryset(*, principal: InventoryMcpPrincipal) -> QuerySet[StockBalance]:
    return scope_queryset_by_identity(
        StockBalance.objects.select_related("inventory_item", "stock_location", "stock_lot").order_by(
            "stock_location__name",
            "inventory_item__name_snapshot",
            "stock_lot__expiry_date",
            "stock_lot__lot_number",
        ),
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

def _list_inventory_items_sync(
    *,
    principal: InventoryMcpPrincipal,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    close_old_connections()
    try:
        queryset = _inventory_item_queryset(principal=principal)
        items = list(queryset)
        scoped_locations = _resolve_structural_scope_locations_sync(
            principal=principal,
            structural_location_id=structural_location_id,
        )
        summary_map = (
            get_inventory_item_summary_map(items, stock_locations=scoped_locations)
            if items
            else {}
        )
        return {
            "profile_id": principal.profile_id,
            "company_code": principal.company_code,
            "count": len(items),
            "results": [
                _inventory_item_payload(item, summary=summary_map.get(item.id, {}))
                for item in items
            ],
        }
    finally:
        close_old_connections()


def _search_inventory_items_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    inventory_type: str | None,
    status: str | None,
    inventory_item_id: str | None,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    close_old_connections()
    try:
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
        scoped_locations = _resolve_structural_scope_locations_sync(
            principal=principal,
            structural_location_id=structural_location_id,
        )
        summary_map = (
            get_inventory_item_summary_map(items, stock_locations=scoped_locations)
            if items and (search_term or inventory_item_id or scoped_locations)
            else {}
        )
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
    finally:
        close_old_connections()


def _get_inventory_item_details_sync(
    *,
    principal: InventoryMcpPrincipal,
    inventory_item_id: str,
    history_limit: int,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    close_old_connections()
    try:
        inventory_item = _inventory_item_queryset(principal=principal).filter(id=inventory_item_id).first()
        if inventory_item is None:
            raise ValueError("Inventory item not found.")
        scoped_locations = _resolve_structural_scope_locations_sync(
            principal=principal,
            structural_location_id=structural_location_id,
        )
        scoped_leaf_location_ids = _resolve_scoped_leaf_location_ids_sync(
            principal=principal,
            structural_location_id=structural_location_id,
        )
        summary = get_inventory_item_summary_map(
            [inventory_item],
            stock_locations=scoped_locations,
        ).get(inventory_item.id, {})
        balances_queryset = _stock_balance_queryset(principal=principal).filter(
            inventory_item_id=inventory_item.id,
        )
        serials_queryset = _stock_serial_queryset(principal=principal).filter(
            inventory_item_id=inventory_item.id,
        )
        reservations_queryset = _stock_reservation_queryset(principal=principal).filter(
            inventory_item_id=inventory_item.id,
        )
        movements_queryset = _stock_movement_queryset(principal=principal).filter(
            inventory_item_id=inventory_item.id,
        )
        if scoped_leaf_location_ids is not None:
            balances_queryset = balances_queryset.filter(stock_location_id__in=scoped_leaf_location_ids)
            serials_queryset = serials_queryset.filter(stock_location_id__in=scoped_leaf_location_ids)
            reservations_queryset = reservations_queryset.filter(stock_location_id__in=scoped_leaf_location_ids)
            movements_queryset = movements_queryset.filter(
                Q(from_location_id__in=scoped_leaf_location_ids)
                | Q(to_location_id__in=scoped_leaf_location_ids)
            )
        balances = list(balances_queryset[:history_limit])
        lots = list(
            _stock_lot_queryset(principal=principal).filter(inventory_item_id=inventory_item.id)[:history_limit]
        )
        serials = list(serials_queryset[:history_limit])
        reservations = list(reservations_queryset[:history_limit])
        movements = list(movements_queryset[:history_limit])
        return {
            "profile_id": principal.profile_id,
            "inventory_item": _inventory_item_payload(inventory_item, summary=summary),
            "balances": [_balance_payload(balance) for balance in balances],
            "lots": [_lot_payload(lot) for lot in lots],
            "serials": [_serial_payload(serial) for serial in serials],
            "active_reservations": [_reservation_payload(reservation) for reservation in reservations],
            "recent_movements": [_movement_payload(movement) for movement in movements],
        }
    finally:
        close_old_connections()


_INVENTORY_CATEGORY_RULES: tuple[dict[str, Any], ...] = (
    {
        "name": "Aftershave",
        "description": "After-shave lotions, shaving fragrance, and related post-shave care products.",
        "keywords": (
            "after shave",
            "after-shave",
            "aftershave",
            "shaving lotion",
            "post shave",
            "post-shave",
        ),
    },
    {
        "name": "Perfume & Fragrances",
        "description": "Perfumes, colognes, eau de parfum, eau de toilette, and fragrance products.",
        "keywords": (
            "perfume",
            "parfum",
            "eau de parfum",
            "eau de toilette",
            "fragrance",
            "cologne",
            "scent",
            "de toilette",
        ),
    },
    {
        "name": "Skincare & Personal Care",
        "description": "Skincare, body care, grooming, and personal care consumables.",
        "keywords": (
            "skin care",
            "skincare",
            "body cream",
            "body lotion",
            "lotion",
            "cream",
            "serum",
            "cleanser",
            "soap",
            "shampoo",
            "conditioner",
            "deodorant",
            "cosmetic",
            "makeup",
        ),
    },
    {
        "name": "Audio Equipment",
        "description": "Headphones, speakers, earbuds, audio accessories, and sound equipment.",
        "keywords": (
            "headphone",
            "headphones",
            "earbud",
            "earbuds",
            "speaker",
            "bluetooth speaker",
            "audio",
            "soundbar",
            "microphone",
        ),
    },
    {
        "name": "Electronics",
        "description": "Phones, computers, cameras, appliances, networking devices, and electronic accessories.",
        "keywords": (
            "phone",
            "iphone",
            "samsung",
            "laptop",
            "computer",
            "tablet",
            "television",
            "tv",
            "router",
            "printer",
            "camera",
            "charger",
            "cable",
            "keyboard",
            "mouse",
            "console",
            "playstation",
            "electronics",
            "electronic",
        ),
    },
    {
        "name": "Watches & Wearables",
        "description": "Watches, smartwatches, fitness bands, and wearable accessories.",
        "keywords": (
            "watch",
            "smartwatch",
            "sport band",
            "fitness band",
            "wearable",
            "wristband",
        ),
    },
    {
        "name": "Eyewear",
        "description": "Sunglasses, glasses, lenses, and eyewear accessories.",
        "keywords": (
            "sunglass",
            "sunglasses",
            "eyeglass",
            "eyeglasses",
            "glasses",
            "spectacles",
            "lens",
            "lenses",
            "eyewear",
        ),
    },
    {
        "name": "Bags & Accessories",
        "description": "Bags, wallets, cases, straps, and fashion accessories.",
        "keywords": (
            "bag",
            "handbag",
            "backpack",
            "wallet",
            "purse",
            "case",
            "strap",
            "accessory",
            "accessories",
        ),
    },
    {
        "name": "Footwear",
        "description": "Shoes, sandals, sneakers, boots, and other footwear.",
        "keywords": (
            "shoe",
            "shoes",
            "sneaker",
            "sneakers",
            "sandal",
            "sandals",
            "boot",
            "boots",
            "footwear",
        ),
    },
    {
        "name": "Apparel",
        "description": "Clothing, shirts, dresses, trousers, sleeves, and other apparel.",
        "keywords": (
            "shirt",
            "t-shirt",
            "dress",
            "trouser",
            "pants",
            "jacket",
            "sleeve",
            "clothing",
            "apparel",
            "wear",
        ),
    },
    {
        "name": "Packaging Materials",
        "description": "Boxes, bottles, labels, wrappers, and inventory packaging materials.",
        "keywords": (
            "packaging",
            "package",
            "box",
            "carton",
            "label",
            "wrapper",
            "bottle",
            "container",
            "glass",
            "veneer box",
        ),
    },
)


def _inventory_category_match_for_item(item: InventoryItem) -> dict[str, Any] | None:
    haystack = " ".join(
        value
        for value in (
            item.name_snapshot,
            item.sku_snapshot,
            item.barcode_snapshot,
            item.description,
            item.inventory_type,
        )
        if value
    ).lower()
    if not haystack:
        return None
    for rule in _INVENTORY_CATEGORY_RULES:
        for keyword in rule["keywords"]:
            if keyword in haystack:
                return {
                    "name": rule["name"],
                    "description": rule["description"],
                    "matched_keyword": keyword,
                }
    return None


def _category_payload_for_auto_categorize(category: InventoryCategory) -> dict[str, Any]:
    return {
        "id": str(category.id),
        "name": category.name,
        "description": category.description or "",
        "structural": category.structural,
        "is_active": category.is_active,
    }


def _auto_categorize_inventory_items_sync(
    *,
    principal: InventoryMcpPrincipal,
    only_uncategorized: bool,
    create_missing_categories: bool,
    apply_changes: bool,
    limit: int,
) -> dict[str, Any]:
    close_old_connections()
    try:
        item_queryset = _inventory_item_queryset(principal=principal).select_related("inventory_category")
        if only_uncategorized:
            item_queryset = item_queryset.filter(inventory_category__isnull=True)
        items = list(item_queryset[: max(1, min(int(limit), 200))])

        category_queryset = scope_queryset_by_identity(
            InventoryCategory.objects.all(),
            canonical_field="profile_id",
            legacy_field="profile",
            value=principal.profile_id,
        ).filter(is_active=True)
        categories_by_name = {
            category.name.strip().casefold(): category
            for category in category_queryset
        }

        created_categories: list[dict[str, Any]] = []
        assigned_items: list[dict[str, Any]] = []
        planned_assignments: list[dict[str, Any]] = []
        skipped_items: list[dict[str, Any]] = []

        for item in items:
            match = _inventory_category_match_for_item(item)
            if match is None:
                skipped_items.append(
                    {
                        "id": str(item.id),
                        "name": item.name_snapshot,
                        "reason": "No confident category match from item name or description.",
                    }
                )
                continue

            category_key = str(match["name"]).casefold()
            category = categories_by_name.get(category_key)
            if category is None:
                if not create_missing_categories or not apply_changes:
                    planned_assignments.append(
                        {
                            "inventory_item_id": str(item.id),
                            "inventory_item_name": item.name_snapshot,
                            "category_name": match["name"],
                            "matched_keyword": match["matched_keyword"],
                            "would_create_category": True,
                        }
                    )
                    continue
                category = InventoryCategory.objects.create(
                    profile_id=principal.profile_id,
                    name=match["name"],
                    description=match["description"],
                    structural=False,
                    is_active=True,
                )
                categories_by_name[category_key] = category
                created_categories.append(_category_payload_for_auto_categorize(category))

            if not apply_changes:
                planned_assignments.append(
                    {
                        "inventory_item_id": str(item.id),
                        "inventory_item_name": item.name_snapshot,
                        "category_id": str(category.id),
                        "category_name": category.name,
                        "matched_keyword": match["matched_keyword"],
                    }
                )
                continue

            item.inventory_category = category
            metadata = dict(item.metadata or {})
            metadata["auto_categorized"] = {
                "source": "inventory_mcp.auto_categorize_inventory_items",
                "category_name": category.name,
                "matched_keyword": match["matched_keyword"],
                "applied_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            }
            item.metadata = metadata
            item.save(update_fields=["inventory_category", "metadata", "updated_at"])
            assigned_items.append(
                {
                    "inventory_item": _inventory_item_payload(item),
                    "category": _category_payload_for_auto_categorize(category),
                    "matched_keyword": match["matched_keyword"],
                }
            )

        return {
            "profile_id": principal.profile_id,
            "company_code": principal.company_code,
            "apply_changes": apply_changes,
            "only_uncategorized": only_uncategorized,
            "processed_count": len(items),
            "created_categories": created_categories,
            "assigned_items": assigned_items,
            "planned_assignments": planned_assignments,
            "skipped_items": skipped_items,
            "summary": {
                "created_category_count": len(created_categories),
                "assigned_item_count": len(assigned_items),
                "planned_assignment_count": len(planned_assignments),
                "skipped_item_count": len(skipped_items),
            },
        }
    finally:
        close_old_connections()


def _assign_inventory_item_category_sync(
    *,
    principal: InventoryMcpPrincipal,
    inventory_item_id: str,
    category_id: str,
) -> dict[str, Any]:
    close_old_connections()
    try:
        item = _inventory_item_queryset(principal=principal).filter(id=inventory_item_id).first()
        if item is None:
            raise ValueError("Inventory item not found for this workspace.")

        category = scope_queryset_by_identity(
            InventoryCategory.objects.filter(id=category_id),
            canonical_field="profile_id",
            legacy_field="profile",
            value=principal.profile_id,
        ).first()
        if category is None:
            raise ValueError("Inventory category not found for this workspace.")
        if category.structural:
            raise ValueError("Cannot assign an inventory item to a structural category.")
        if not category.is_active:
            raise ValueError("Cannot assign an inventory item to an inactive category.")

        item.inventory_category = category
        metadata = dict(item.metadata or {})
        metadata["category_assignment"] = {
            "source": "inventory_mcp.assign_inventory_item_category",
            "category_id": str(category.id),
            "category_name": category.name,
            "applied_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        item.metadata = metadata
        item.save(update_fields=["inventory_category", "metadata", "updated_at"])
        item.refresh_from_db(fields=["inventory_category", "metadata", "updated_at"])

        verified = str(item.inventory_category_id or "") == str(category.id)
        return {
            "profile_id": principal.profile_id,
            "assigned": verified,
            "inventory_item": _inventory_item_payload(item),
            "category": _category_payload_for_auto_categorize(category),
        }
    finally:
        close_old_connections()


_INVENTORY_CONTROL_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "profile": "serialized_electronics",
        "keywords": (
            "phone",
            "iphone",
            "galaxy",
            "laptop",
            "computer",
            "tablet",
            "camera",
            "console",
            "printer",
            "router",
            "watch",
            "headphone",
            "earbud",
            "speaker",
        ),
        "controls": {
            "minimum_stock_level": Decimal("2"),
            "safety_stock_level": Decimal("3"),
            "reorder_point": Decimal("5"),
            "reorder_quantity": Decimal("10"),
            "track_lot": False,
            "track_serial": True,
            "track_expiry": False,
            "allow_negative_stock": False,
        },
        "reason": "High-value durable electronics should usually be serial-tracked, protected from negative stock, and replenished before stockout.",
    },
    {
        "profile": "fragrance_and_cosmetics",
        "keywords": (
            "perfume",
            "parfum",
            "fragrance",
            "cologne",
            "aftershave",
            "after-shave",
            "eau de parfum",
            "eau de toilette",
            "lotion",
            "cream",
            "serum",
            "cosmetic",
        ),
        "controls": {
            "minimum_stock_level": Decimal("3"),
            "safety_stock_level": Decimal("5"),
            "reorder_point": Decimal("8"),
            "reorder_quantity": Decimal("20"),
            "track_lot": True,
            "track_serial": False,
            "track_expiry": True,
            "allow_negative_stock": False,
        },
        "reason": "Fragrance and cosmetics should usually be lot/expiry tracked and replenished in modest batches.",
    },
    {
        "profile": "apparel_footwear_accessories",
        "keywords": (
            "shoe",
            "sneaker",
            "sandal",
            "boot",
            "shirt",
            "dress",
            "trouser",
            "bag",
            "wallet",
            "belt",
            "sunglass",
            "eyewear",
            "accessory",
        ),
        "controls": {
            "minimum_stock_level": Decimal("4"),
            "safety_stock_level": Decimal("6"),
            "reorder_point": Decimal("10"),
            "reorder_quantity": Decimal("24"),
            "track_lot": False,
            "track_serial": False,
            "track_expiry": False,
            "allow_negative_stock": False,
        },
        "reason": "Apparel, footwear, and accessories are usually size/style stock, not serial or expiry stock; moderate replenishment buffers are safer.",
    },
    {
        "profile": "food_pharma_perishable",
        "keywords": (
            "food",
            "drink",
            "beverage",
            "medicine",
            "drug",
            "pharma",
            "supplement",
            "vitamin",
            "perishable",
            "expiry",
        ),
        "controls": {
            "minimum_stock_level": Decimal("5"),
            "safety_stock_level": Decimal("10"),
            "reorder_point": Decimal("15"),
            "reorder_quantity": Decimal("30"),
            "track_lot": True,
            "track_serial": False,
            "track_expiry": True,
            "allow_negative_stock": False,
        },
        "reason": "Perishable and regulated goods should be lot/expiry tracked with stronger buffers.",
    },
    {
        "profile": "packaging_consumables",
        "keywords": (
            "packaging",
            "package",
            "box",
            "carton",
            "label",
            "wrapper",
            "bottle",
            "container",
            "consumable",
        ),
        "controls": {
            "minimum_stock_level": Decimal("20"),
            "safety_stock_level": Decimal("50"),
            "reorder_point": Decimal("100"),
            "reorder_quantity": Decimal("250"),
            "track_lot": False,
            "track_serial": False,
            "track_expiry": False,
            "allow_negative_stock": False,
        },
        "reason": "Consumables and packaging usually need volume-based reorder buffers, not serial or expiry tracking.",
    },
)


def _decimal_control_to_float(value: Any) -> float:
    try:
        return float(Decimal(str(value)))
    except Exception:
        return 0.0


def _inventory_control_profile_for_text(text: str) -> dict[str, Any]:
    normalized = (text or "").lower()
    for profile in _INVENTORY_CONTROL_PROFILES:
        if any(keyword in normalized for keyword in profile["keywords"]):
            return profile
    return {
        "profile": "general_stock",
        "controls": {
            "minimum_stock_level": Decimal("2"),
            "safety_stock_level": Decimal("3"),
            "reorder_point": Decimal("5"),
            "reorder_quantity": Decimal("10"),
            "track_lot": False,
            "track_serial": False,
            "track_expiry": False,
            "allow_negative_stock": False,
        },
        "reason": "General stock should still have non-zero reorder controls unless the user explicitly disables replenishment.",
    }


def _inventory_control_recommendation_payload(
    *,
    item_name: str,
    category_name: str = "",
    description: str = "",
    current_controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = " ".join(value for value in (item_name, category_name, description) if value)
    profile = _inventory_control_profile_for_text(text)
    controls = dict(profile["controls"])
    existing = current_controls or {}
    zero_or_blank_thresholds = all(
        _decimal_control_to_float(existing.get(field, 0)) == 0
        for field in ("minimum_stock_level", "safety_stock_level", "reorder_point", "reorder_quantity")
    )
    return {
        "profile": profile["profile"],
        "item_name": item_name,
        "category_name": category_name,
        "recommended_controls": {
            key: _decimal_to_float(value) if isinstance(value, Decimal) else value
            for key, value in controls.items()
        },
        "reason": profile["reason"],
        "should_apply": zero_or_blank_thresholds,
        "notes": (
            "Current replenishment thresholds are all zero, so these recommendations should be applied before creating or updating the item."
            if zero_or_blank_thresholds
            else "Existing replenishment thresholds are already set; do not overwrite them unless the user asks for a recalibration."
        ),
    }


def _recommend_inventory_item_controls_sync(
    *,
    principal: InventoryMcpPrincipal,
    inventory_item_id: str | None,
    item_name: str,
    category_name: str,
    description: str,
    apply_changes: bool,
) -> dict[str, Any]:
    close_old_connections()
    try:
        item: InventoryItem | None = None
        current_controls: dict[str, Any] = {}
        if inventory_item_id:
            item = _inventory_item_queryset(principal=principal).filter(id=inventory_item_id).first()
            if item is None:
                raise ValueError("inventory_item_id was not found for this workspace")
            item_name = item.name_snapshot
            category_name = item.inventory_category.name if item.inventory_category_id else category_name
            description = item.description or description
            current_controls = {
                "minimum_stock_level": item.minimum_stock_level,
                "safety_stock_level": item.safety_stock_level,
                "reorder_point": item.reorder_point,
                "reorder_quantity": item.reorder_quantity,
                "track_lot": item.track_lot,
                "track_serial": item.track_serial,
                "track_expiry": item.track_expiry,
                "allow_negative_stock": item.allow_negative_stock,
            }

        recommendation = _inventory_control_recommendation_payload(
            item_name=item_name,
            category_name=category_name,
            description=description,
            current_controls=current_controls,
        )

        applied = False
        if apply_changes and item is not None and recommendation["should_apply"]:
            controls = recommendation["recommended_controls"]
            for field in (
                "minimum_stock_level",
                "safety_stock_level",
                "reorder_point",
                "reorder_quantity",
                "track_lot",
                "track_serial",
                "track_expiry",
                "allow_negative_stock",
            ):
                setattr(item, field, controls[field])
            metadata = dict(item.metadata or {})
            metadata["inventory_control_recommendation"] = {
                "source": "inventory_mcp.recommend_inventory_item_controls",
                "profile": recommendation["profile"],
                "applied_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            }
            item.metadata = metadata
            item.save(
                update_fields=[
                    "minimum_stock_level",
                    "safety_stock_level",
                    "reorder_point",
                    "reorder_quantity",
                    "track_lot",
                    "track_serial",
                    "track_expiry",
                    "allow_negative_stock",
                    "metadata",
                    "updated_at",
                ]
            )
            applied = True

        return {
            "apply_changes": apply_changes,
            "applied": applied,
            "inventory_item": _inventory_item_payload(item) if item else None,
            **recommendation,
        }
    finally:
        close_old_connections()


def _get_inventory_alerts_sync(
    *,
    principal: InventoryMcpPrincipal,
    limit: int,
    expiring_days: int,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    close_old_connections()
    try:
        inventory_queryset = (
            _inventory_queryset(principal=principal)
            .exclude(status__in=["archived", "discontinued"])
            .select_related("inventory_category")
        )
        inventory_rows = list(
            inventory_queryset.values(
                "id",
                "minimum_stock_level",
                "reorder_point",
            )
        )
        inventory_ids = [row["id"] for row in inventory_rows if row.get("id") is not None]
        scoped_location_ids = _resolve_scoped_leaf_location_ids_sync(
            principal=principal,
            structural_location_id=structural_location_id,
        )
        balance_queryset = _stock_balance_queryset(principal=principal).filter(
            inventory_item_id__in=inventory_ids
        )
        if scoped_location_ids:
            balance_queryset = balance_queryset.filter(stock_location_id__in=scoped_location_ids)
        aggregate_rows = balance_queryset.values("inventory_item_id").annotate(
            quantity=Sum("quantity_on_hand"),
            quantity_reserved=Sum("quantity_reserved"),
            quantity_available=Sum("quantity_available"),
            location_count=Count("stock_location_id", distinct=True),
        )
        summary_map = {
            row["inventory_item_id"]: {
                "quantity": row.get("quantity") or Decimal("0"),
                "quantity_reserved": row.get("quantity_reserved") or Decimal("0"),
                "quantity_available": row.get("quantity_available") or Decimal("0"),
                "location_count": int(row.get("location_count") or 0),
            }
            for row in aggregate_rows
            if row.get("inventory_item_id") is not None
        }
        low_stock_ids: list[Any] = []
        needs_reorder_ids: list[Any] = []
        out_of_stock_ids: list[Any] = []
        expiring = []

        for inventory_row in inventory_rows:
            inventory_id = inventory_row.get("id")
            if inventory_id is None:
                continue
            summary = summary_map.get(
                inventory_id,
                {
                    "quantity": Decimal("0"),
                    "quantity_reserved": Decimal("0"),
                    "quantity_available": Decimal("0"),
                    "location_count": 0,
                },
            )
            # An item without a balance has not been placed in a stock location yet.
            # Treat that as an inventory-setup gap, not a confirmed stockout.
            if not int(summary.get("location_count") or 0):
                continue
            current_stock = Decimal(summary.get("quantity") or Decimal("0"))
            minimum_stock_level = Decimal(str(inventory_row.get("minimum_stock_level") or 0))
            reorder_point = Decimal(str(inventory_row.get("reorder_point") or 0))
            if current_stock <= 0:
                out_of_stock_ids.append(inventory_id)
            elif minimum_stock_level > 0 and current_stock <= minimum_stock_level:
                low_stock_ids.append(inventory_id)
            elif reorder_point > 0 and current_stock <= reorder_point:
                needs_reorder_ids.append(inventory_id)
            # The alert-first MCP path intentionally skips expiry-lot traversal to keep
            # stock-risk queries responsive; expiry-specific flows use movement/detail tools.

        selected_ids = {
            *out_of_stock_ids[:limit],
            *low_stock_ids[:limit],
            *needs_reorder_ids[:limit],
        }
        inventory_map = {
            item.id: item
            for item in inventory_queryset.filter(id__in=selected_ids)
        }
        scoped_locations = _resolve_structural_scope_locations_sync(
            principal=principal,
            structural_location_id=structural_location_id,
        )
        item_summaries = (
            get_inventory_item_summary_map(
                list(inventory_map.values()),
                stock_locations=scoped_locations,
            )
            if inventory_map
            else {}
        )

        def _payload_for(item_id: Any) -> dict[str, Any] | None:
            item = inventory_map.get(item_id)
            if item is None:
                return None
            return _inventory_item_payload(
                item,
                # The alert aggregate decides risk membership. The canonical item
                # summary supplies structural location names and product metadata.
                summary=item_summaries.get(
                    item_id,
                    {
                        "quantity": Decimal("0"),
                        "quantity_reserved": Decimal("0"),
                        "quantity_available": Decimal("0"),
                        "location_count": 0,
                    },
                ),
            )

        def _risk_payloads(item_ids: list[Any], category: str) -> list[dict[str, Any]]:
            return [
                {**payload, "risk_category": category}
                for payload in (_payload_for(item_id) for item_id in item_ids[:limit])
                if payload
            ]

        out_of_stock = _risk_payloads(out_of_stock_ids, "out_of_stock")
        low_stock = _risk_payloads(low_stock_ids, "low_stock")
        needs_reorder = _risk_payloads(needs_reorder_ids, "needs_reorder")

        return {
            "profile_id": principal.profile_id,
            "expiring_days": expiring_days,
            "limit_per_category": limit,
            "summary": {
                "low_stock_count": len(low_stock_ids),
                "reorder_count": len(needs_reorder_ids),
                "out_of_stock_count": len(out_of_stock_ids),
                "expiring_count": len(expiring),
            },
            "low_stock": low_stock,
            "needs_reorder": needs_reorder,
            "out_of_stock": out_of_stock,
            "expiring_soon": [
                {**item, "risk_category": "expiring_soon"}
                for item in expiring[:limit]
                if isinstance(item, dict)
            ],
        }
    finally:
        close_old_connections()


def _get_stock_risk_sync(
    *,
    principal: InventoryMcpPrincipal,
    limit: int,
    expiring_days: int,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    alerts = _get_inventory_alerts_sync(
        principal=principal,
        limit=limit,
        expiring_days=expiring_days,
        structural_location_id=structural_location_id,
    )
    return {
        "profile_id": principal.profile_id,
        "limit_per_category": alerts.get("limit_per_category", limit),
        "summary": {
            **(alerts.get("summary") if isinstance(alerts.get("summary"), dict) else {}),
            "low_stock_count": int((alerts.get("summary") or {}).get("low_stock_count") or len(alerts["low_stock"])),
            "reorder_count": int((alerts.get("summary") or {}).get("reorder_count") or len(alerts["needs_reorder"])),
            "out_of_stock_count": int((alerts.get("summary") or {}).get("out_of_stock_count") or len(alerts["out_of_stock"])),
            "expiring_count": int((alerts.get("summary") or {}).get("expiring_count") or len(alerts["expiring_soon"])),
        },
        "risk_items": {
            "out_of_stock": alerts["out_of_stock"],
            "needs_reorder": alerts["needs_reorder"],
            "low_stock": alerts["low_stock"],
            "expiring_soon": alerts["expiring_soon"],
        },
    }


def _get_reorder_candidates_sync(
    *,
    principal: InventoryMcpPrincipal,
    limit: int,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    alerts = _get_inventory_alerts_sync(
        principal=principal,
        limit=limit,
        expiring_days=30,
        structural_location_id=structural_location_id,
    )
    candidates = [*alerts["out_of_stock"], *alerts["needs_reorder"]][:limit]
    return {
        "profile_id": principal.profile_id,
        "count": len(candidates),
        "results": candidates,
    }


def _get_po_pipeline_sync(*, principal: InventoryMcpPrincipal, limit: int) -> dict[str, Any]:
    close_old_connections()
    try:
        queryset = scope_queryset_by_identity(
            PurchaseOrder.objects.select_related("supplier"),
            canonical_field="profile_id",
            legacy_field="profile",
            value=principal.profile_id,
        )
        orders = list(queryset.order_by("-created_at")[: max(1, min(limit, 50))])
        status_counts = {
            row["status"]: int(row["count"])
            for row in queryset.values("status").annotate(count=Count("id")).order_by()
        }
        return {
            "profile_id": principal.profile_id,
            "status_counts": status_counts,
            "results": [
                {
                    "id": str(order.id),
                    "reference": order.reference,
                    "status": order.status,
                    "supplier_name": getattr(order.supplier, "name", ""),
                    "created_at": order.created_at.isoformat() if order.created_at else None,
                    "target_date": _purchase_order_target_date(order),
                }
                for order in orders
            ],
        }
    finally:
        close_old_connections()


def _get_receiving_exceptions_sync(*, principal: InventoryMcpPrincipal, limit: int) -> dict[str, Any]:
    close_old_connections()
    try:
        queryset = (
            scope_queryset_by_identity(
                PurchaseOrder.objects.prefetch_related("line_items"),
                canonical_field="profile_id",
                legacy_field="profile",
                value=principal.profile_id,
            )
            .filter(status__in=[
                PurchaseOrderStatus.APPROVED,
                PurchaseOrderStatus.ISSUED,
                PurchaseOrderStatus.OVERDUE,
                PurchaseOrderStatus.RECEIVED,
            ])
            .order_by("-updated_at")
        )
        results: list[dict[str, Any]] = []
        for order in queryset:
            open_lines = 0
            remaining_quantity = Decimal("0")
            for line_item in order.line_items.all():
                if line_item.remaining_quantity > 0:
                    open_lines += 1
                    remaining_quantity += Decimal(str(line_item.remaining_quantity))
            if open_lines <= 0 and order.status != PurchaseOrderStatus.OVERDUE:
                continue
            results.append(
                {
                    "id": str(order.id),
                    "reference": order.reference,
                    "status": order.status,
                    "supplier_name": getattr(order.supplier, "name", ""),
                    "target_date": _purchase_order_target_date(order),
                    "open_line_count": open_lines,
                    "remaining_quantity": _decimal_to_float(remaining_quantity),
                }
            )
            if len(results) >= max(1, min(limit, 50)):
                break
        return {
            "profile_id": principal.profile_id,
            "count": len(results),
            "results": results,
        }
    finally:
        close_old_connections()


def _search_stock_locations_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    structural_only: bool | None,
    external_only: bool | None,
) -> dict[str, Any]:
    close_old_connections()
    try:
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
                _location_payload(
                    location,
                    summary=get_location_stock_summary(location) if search_term else None,
                )
                for location in locations
            ],
        }
    finally:
        close_old_connections()


def _list_stock_location_types_sync(
    *,
    query: str | None,
    limit: int,
) -> dict[str, Any]:
    queryset = _stock_location_type_queryset()
    search_term = str(query or "").strip()
    if search_term:
        queryset = queryset.filter(Q(name__icontains=search_term) | Q(description__icontains=search_term))
    location_types = list(queryset[:limit])
    return {
        "query": search_term or None,
        "count": len(location_types),
        "results": [_location_type_payload(item) for item in location_types],
    }


_STOCK_LOCATION_TYPE_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "warehouse": ("warehouse", "main warehouse", "central warehouse", "storage facility"),
    "showroom": ("showroom", "front store", "store", "retail floor", "sales floor"),
    "backroom": ("backroom", "back room", "stock room", "storage room"),
    "returns area": ("returns area", "returns shelf", "returns rack", "returns processing"),
    "overflow": ("overflow", "overflow room"),
    "shelf": ("shelf",),
    "rack": ("rack",),
    "bin": ("bin",),
    "wardrobe": ("wardrobe",),
}


def _normalize_stock_location_type_token(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").replace("_", " ").split())


def _match_stock_location_type_name(
    location_types: list[StockLocationType],
    *,
    requested_name: str | None,
    location_name: str | None,
    structural: bool,
    parent_id: str | None,
) -> str | None:
    normalized_requested = _normalize_stock_location_type_token(requested_name)
    normalized_location_name = _normalize_stock_location_type_token(location_name)

    alias_to_canonical: dict[str, str] = {}
    for canonical, aliases in _STOCK_LOCATION_TYPE_ALIAS_MAP.items():
        alias_to_canonical[canonical] = canonical
        for alias in aliases:
            alias_to_canonical[_normalize_stock_location_type_token(alias)] = canonical

    if normalized_requested:
        canonical = alias_to_canonical.get(normalized_requested, normalized_requested)
        for item in location_types:
            if _normalize_stock_location_type_token(item.name) == canonical:
                return item.name

    if normalized_location_name:
        for alias, canonical in alias_to_canonical.items():
            if alias and alias in normalized_location_name:
                for item in location_types:
                    if _normalize_stock_location_type_token(item.name) == canonical:
                        return item.name

    fallback_names = ["warehouse"] if structural and not parent_id else ["backroom", "shelf", "showroom", "warehouse"]
    for fallback in fallback_names:
        for item in location_types:
            if _normalize_stock_location_type_token(item.name) == fallback:
                return item.name
    return None


def _prepare_stock_location_payload_data(
    *,
    data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return data

    payload = dict(data)
    location_types = list(_stock_location_type_queryset())
    requested_type_name = payload.pop("location_type_name", None)
    structural = bool(payload.get("structural"))
    parent_id = str(payload.get("parent_id") or "").strip() or None
    matched_type_name = _match_stock_location_type_name(
        location_types,
        requested_name=str(requested_type_name or "").strip() or None,
        location_name=str(payload.get("name") or "").strip() or None,
        structural=structural,
        parent_id=parent_id,
    )
    if matched_type_name and not payload.get("location_type_id"):
        match = next(
            (
                item
                for item in location_types
                if _normalize_stock_location_type_token(item.name)
                == _normalize_stock_location_type_token(matched_type_name)
            ),
            None,
        )
        if match is not None:
            payload["location_type_id"] = str(match.id)
    return payload


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


def _search_stock_lots_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    inventory_item_id: str | None,
    status: str | None,
) -> dict[str, Any]:
    queryset = _stock_lot_queryset(principal=principal)
    if inventory_item_id:
        queryset = queryset.filter(inventory_item_id=inventory_item_id)
    if status:
        queryset = queryset.filter(status=status)

    search_term = str(query or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(lot_number__icontains=search_term)
            | Q(inventory_item__name_snapshot__icontains=search_term)
        )

    lots = list(queryset[:limit])
    return {
        "query": search_term or None,
        "count": len(lots),
        "limit": limit,
        "profile_id": principal.profile_id,
        "results": [_lot_payload(lot) for lot in lots],
    }


def _search_stock_serials_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    inventory_item_id: str | None,
    status: str | None,
) -> dict[str, Any]:
    queryset = _stock_serial_queryset(principal=principal)
    if inventory_item_id:
        queryset = queryset.filter(inventory_item_id=inventory_item_id)
    if status:
        queryset = queryset.filter(status=status)

    search_term = str(query or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(serial_number__icontains=search_term)
            | Q(inventory_item__name_snapshot__icontains=search_term)
            | Q(stock_location__name__icontains=search_term)
            | Q(stock_lot__lot_number__icontains=search_term)
        )

    serials = list(queryset[:limit])
    return {
        "query": search_term or None,
        "count": len(serials),
        "limit": limit,
        "profile_id": principal.profile_id,
        "results": [_serial_payload(serial) for serial in serials],
    }


def _search_stock_balances_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    inventory_item_id: str | None,
    location_id: str | None,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    queryset = _stock_balance_queryset(principal=principal).filter(
        Q(quantity_on_hand__gt=0) | Q(quantity_reserved__gt=0)
    )
    if inventory_item_id:
        queryset = queryset.filter(inventory_item_id=inventory_item_id)
    if location_id:
        queryset = queryset.filter(stock_location_id=location_id)
    scoped_leaf_location_ids = _resolve_scoped_leaf_location_ids_sync(
        principal=principal,
        structural_location_id=structural_location_id,
    )
    if scoped_leaf_location_ids is not None:
        queryset = queryset.filter(stock_location_id__in=scoped_leaf_location_ids)

    search_term = str(query or "").strip()
    if search_term:
        queryset = queryset.filter(
            Q(inventory_item__name_snapshot__icontains=search_term)
            | Q(stock_location__name__icontains=search_term)
            | Q(stock_lot__lot_number__icontains=search_term)
        )

    balances = list(queryset[:limit])
    return {
        "query": search_term or None,
        "count": len(balances),
        "limit": limit,
        "profile_id": principal.profile_id,
        "results": [_balance_payload(balance) for balance in balances],
    }


def _search_stock_movements_sync(
    *,
    principal: InventoryMcpPrincipal,
    query: str | None,
    limit: int,
    movement_type: str | None,
    inventory_item_id: str | None,
    reference_id: str | None,
    date_from: str | None,
    date_to: str | None,
) -> dict[str, Any]:
    queryset = _stock_movement_queryset(principal=principal)
    if movement_type:
        queryset = queryset.filter(movement_type=movement_type)
    if inventory_item_id:
        queryset = queryset.filter(inventory_item_id=inventory_item_id)
    if reference_id:
        queryset = queryset.filter(reference_id=reference_id)
    if date_from:
        queryset = queryset.filter(occurred_at__date__gte=str(date_from).strip())
    if date_to:
        queryset = queryset.filter(occurred_at__date__lte=str(date_to).strip())

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


def _get_stock_analytics_sync(
    *,
    principal: InventoryMcpPrincipal,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    scoped_locations = _resolve_structural_scope_locations_sync(
        principal=principal,
        structural_location_id=structural_location_id,
    )
    analytics = get_profile_stock_analytics(
        profile_id=principal.profile_id,
        stock_locations=scoped_locations,
    )
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
    close_old_connections()
    factory = APIRequestFactory()
    http_method = method.lower().strip()
    path = "/mcp/internal"
    request_headers = {
        "HTTP_AUTHORIZATION": f"Bearer {principal.token}",
        "HTTP_HOST": "localhost",
    }
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
    if http_method == "get":
        request = factory.get(path, data=sanitized_query_params, format="json", **request_headers)
    elif http_method == "post":
        request = factory.post(path, data=data or {}, format="json", **request_headers)
    elif http_method == "patch":
        request = factory.patch(path, data=data or {}, format="json", **request_headers)
    elif http_method == "put":
        request = factory.put(path, data=data or {}, format="json", **request_headers)
    elif http_method == "delete":
        request = factory.delete(path, data=data or {}, format="json", **request_headers)
    else:
        raise ValueError(f"Unsupported method: {method}")

    view = viewset_cls.as_view({http_method: action})
    try:
        response = view(request, pk=pk) if pk is not None else view(request)
    finally:
        close_old_connections()
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
    date_from: str | None,
    date_to: str | None,
) -> dict[str, Any]:
    from mainapps.orders.views import PurchaseOrderViewSet

    normalized_status = str(status or "").strip().lower() or None
    status_filter = "active" if normalized_status == "open" else ""
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=PurchaseOrderViewSet,
        action="list",
        method="get",
        query_params={
            "search": str(query or "").strip(),
            "status": "" if status_filter else (status or ""),
            "status_filter": status_filter,
            "date_from": str(date_from or "").strip(),
            "date_to": str(date_to or "").strip(),
            "page_size": limit,
        },
    )
    if isinstance(payload, list):
        payload = {
            "count": len(payload),
            "next": None,
            "previous": None,
            "results": payload,
        }
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


def _get_purchase_order_analytics_sync(
    *,
    principal: InventoryMcpPrincipal,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    from mainapps.orders.views import PurchaseOrderViewSet

    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=PurchaseOrderViewSet,
        action="analytics",
        method="get",
        query_params={
            "date_from": str(date_from or "").strip(),
            "date_to": str(date_to or "").strip(),
        },
    )
    payload = _to_json_compatible(payload)
    return {
        "profile_id": principal.profile_id,
        "date_from": str(date_from or "").strip() or None,
        "date_to": str(date_to or "").strip() or None,
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
    close_old_connections()
    try:
        from mainapps.orders.models import SalesOrder

        queryset = SalesOrder.objects.select_related("customer").order_by("-created_at")
        queryset = scope_queryset_by_identity(
            queryset,
            canonical_field="profile_id",
            legacy_field="profile",
            value=principal.profile_id,
        )
        search_term = str(query or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(reference__icontains=search_term)
                | Q(description__icontains=search_term)
                | Q(customer_reference__icontains=search_term)
                | Q(customer__name__icontains=search_term)
            )
        normalized_status = str(status or "").strip().lower()
        if normalized_status:
            queryset = queryset.filter(status=normalized_status)
        records = list(
            queryset.values(
                "id",
                "reference",
                "status",
                "customer_id",
                "customer_reference",
                "description",
                "notes",
                "issue_date",
                "shipment_date",
                "delivery_date",
                "received_date",
            )[: max(limit, 1)]
        )
        payload = {
            "count": len(records),
            "next": None,
            "previous": None,
            "results": records,
        }
        return {
            "profile_id": principal.profile_id,
            "query": search_term or None,
            "status": status,
            "results": payload,
        }
    finally:
        close_old_connections()


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
    close_old_connections()
    try:
        from mainapps.orders.models import ReturnOrder

        queryset = ReturnOrder.objects.select_related("purchase_order").order_by("-created_at")
        queryset = scope_queryset_by_identity(
            queryset,
            canonical_field="profile_id",
            legacy_field="profile",
            value=principal.profile_id,
        )
        search_term = str(query or "").strip()
        if search_term:
            queryset = queryset.filter(
                Q(reference__icontains=search_term)
                | Q(purchase_order__reference__icontains=search_term)
            )
        normalized_status = str(status or "").strip().lower()
        if normalized_status:
            queryset = queryset.filter(status=normalized_status)
        records = list(
            queryset.values(
                "id",
                "reference",
                "status",
                "purchase_order_id",
                "customer_id",
                "customer_reference",
                "return_reason",
                "description",
                "notes",
                "issue_date",
                "delivery_date",
                "received_date",
            )[: max(limit, 1)]
        )
        payload = {
            "count": len(records),
            "next": None,
            "previous": None,
            "results": records,
        }
        return {
            "profile_id": principal.profile_id,
            "query": search_term or None,
            "status": status,
            "results": payload,
        }
    finally:
        close_old_connections()


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


def _adjust_inventory_item_stock_via_view_sync(
    *,
    principal: InventoryMcpPrincipal,
    inventory_item_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    request_data = dict(data)
    adjustments = request_data.get("adjustments")
    if isinstance(adjustments, list) and adjustments:
        first_adjustment = adjustments[0] if isinstance(adjustments[0], dict) else {}
        stock_location_id = first_adjustment.get("stock_location_id")
        structural_location_id = first_adjustment.get("structural_location_id")
        quantity = first_adjustment.get("quantity")
        adjustment_type = str(first_adjustment.get("adjustment_type") or "").strip().lower()
        if stock_location_id and "location_id" not in request_data:
            request_data["location_id"] = stock_location_id
        if structural_location_id and "structural_location_id" not in request_data:
            request_data["structural_location_id"] = structural_location_id
        if quantity not in (None, "") and "quantity_change" not in request_data:
            quantity_change = Decimal(str(quantity))
            if adjustment_type == "remove" and quantity_change > 0:
                quantity_change = -quantity_change
            request_data["quantity_change"] = str(quantity_change)
        if first_adjustment.get("notes") and "notes" not in request_data:
            request_data["notes"] = first_adjustment.get("notes")
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=InventoryCatalogItemViewSet,
        action="adjust_stock",
        method="post",
        pk=inventory_item_id,
        data=request_data,
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
    request_data = dict(data)
    transfers = request_data.get("transfers")
    if isinstance(transfers, list) and transfers:
        first_transfer = transfers[0] if isinstance(transfers[0], dict) else {}
        for source_key, target_key in (
            ("inventory_item_id", "inventory_item_id"),
            ("to_location_id", "to_location_id"),
            ("stock_lot_id", "stock_lot_id"),
            ("stock_serial_id", "stock_serial_id"),
            ("quantity", "quantity"),
            ("notes", "notes"),
        ):
            if first_transfer.get(source_key) not in (None, "") and target_key not in request_data:
                request_data[target_key] = first_transfer.get(source_key)
        if first_transfer.get("structural_location_id") not in (None, "") and "structural_location_id" not in request_data:
            request_data["structural_location_id"] = first_transfer.get("structural_location_id")
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=StockLocationViewSet,
        action="transfer_stock",
        method="post",
        pk=location_id,
        data=request_data,
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
    request_data = dict(data)
    if "stock_location_id" in request_data and "location_id" not in request_data:
        request_data["location_id"] = request_data["stock_location_id"]
    if "reserved_quantity" in request_data and "quantity" not in request_data:
        request_data["quantity"] = request_data["reserved_quantity"]
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=StockReservationViewSet,
        action="create",
        method="post",
        data=request_data,
    )
    return {
        "profile_id": principal.profile_id,
        "reservation": _normalize_reservation_payload(payload),
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
        "reservation": _normalize_reservation_payload(payload),
    }


def _normalize_reservation_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return payload
    normalized = dict(payload)
    if normalized.get("inventory_item") and "inventory_item_id" not in normalized:
        normalized["inventory_item_id"] = normalized.get("inventory_item")
    if normalized.get("stock_location") and "stock_location_id" not in normalized:
        normalized["stock_location_id"] = normalized.get("stock_location")
    if normalized.get("location_name") and "stock_location_name" not in normalized:
        normalized["stock_location_name"] = normalized.get("location_name")
    if normalized.get("stock_lot") and "stock_lot_id" not in normalized:
        normalized["stock_lot_id"] = normalized.get("stock_lot")
    if normalized.get("stock_serial") and "stock_serial_id" not in normalized:
        normalized["stock_serial_id"] = normalized.get("stock_serial")
    for key in (
        "inventory_item_id",
        "stock_location_id",
        "stock_lot_id",
        "stock_serial_id",
    ):
        value = normalized.get(key)
        if value not in (None, ""):
            normalized[key] = str(value)
    return normalized


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


def _inventory_item_crud_action_sync(
    *,
    principal: InventoryMcpPrincipal,
    action: str,
    method: str,
    inventory_item_id: str | None = None,
    data: dict[str, Any] | None = None,
    query_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prepared_data = _prepare_inventory_item_payload_data(data=data)
    if action == "create" and "profile_id" not in prepared_data:
        prepared_data["profile_id"] = principal.profile_id
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=InventoryCatalogItemViewSet,
        action=action,
        method=method,
        pk=inventory_item_id,
        data=prepared_data,
        query_params=query_params,
    )
    return {
        "profile_id": principal.profile_id,
        "inventory_item": payload,
    }


def _bulk_update_inventory_item_controls_sync(
    *,
    principal: InventoryMcpPrincipal,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=InventoryCatalogItemViewSet,
        action="bulk_update_controls",
        method="post",
        data=data or {},
    )
    if not isinstance(payload, dict):
        raise ValueError("bulk_update_controls returned an unexpected response payload")
    return {
        "profile_id": principal.profile_id,
        "updated_count": int(payload.get("updated_count") or 0),
        "skipped_count": int(payload.get("skipped_count") or 0),
        "results": payload.get("results") or [],
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
    prepared_data = _prepare_stock_location_payload_data(data=data)
    payload = _invoke_view_action_sync(
        principal=principal,
        viewset_cls=StockLocationViewSet,
        action=action,
        method=method,
        pk=location_id,
        data=prepared_data,
        query_params=query_params,
    )
    return {
        "profile_id": principal.profile_id,
        "location": payload,
    }


def _inventory_item_action_sync(
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
        viewset_cls=InventoryOperationsItemViewSet,
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

@mcp.tool(
    name="list_inventory_items",
    description="Retrieve all inventory items for the authenticated workspace.",
)
async def list_inventory_items(
    structural_location_id: str | None = None,
) -> inventory_payloads.InventoryItemCollectionResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_list_inventory_items_sync, thread_sensitive=True)(
        principal=principal,
        structural_location_id=str(structural_location_id).strip() if structural_location_id else None,
    )
@mcp.tool(
    name="search_inventory_items",
    description="Search inventory item records by name, SKU, barcode, or description.",
)
async def search_inventory_items(
    query: str | None = None,
    limit: int = 10,
    inventory_type: str | None = None,
    status: str | None = None,
    inventory_item_id: str | None = None,
    structural_location_id: str | None = None,
) -> inventory_payloads.InventoryItemCollectionResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    return await sync_to_async(_search_inventory_items_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        limit=limit_value,
        inventory_type=inventory_type,
        status=status,
        inventory_item_id=str(inventory_item_id).strip() if inventory_item_id else None,
        structural_location_id=str(structural_location_id).strip() if structural_location_id else None,
    )


@mcp.tool(
    name="get_inventory_item_details",
    description="Get deep detail for an inventory item, including lots, serials, reservations, and recent movements.",
)
async def get_inventory_item_details(
    inventory_item_id: str,
    history_limit: int = 10,
    structural_location_id: str | None = None,
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
        structural_location_id=str(structural_location_id).strip() if structural_location_id else None,
    )


@mcp.tool(
    name="get_inventory_alerts",
    description="Return low-stock, reorder, out-of-stock, and expiring inventory queues.",
)
async def get_inventory_alerts(
    limit: int = 10,
    expiring_days: int = 30,
    structural_location_id: str | None = None,
) -> inventory_payloads.InventoryAlertsResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    day_window = max(1, min(int(expiring_days), 365))
    return await sync_to_async(_get_inventory_alerts_sync, thread_sensitive=True)(
        principal=principal,
        limit=limit_value,
        expiring_days=day_window,
        structural_location_id=str(structural_location_id).strip() if structural_location_id else None,
    )


@mcp.tool(
    name="get_stock_risk",
    description="Return stock-risk counts and the highest-risk inventory items for the active workspace.",
)
async def get_stock_risk(
    limit: int = 10,
    expiring_days: int = 30,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_stock_risk_sync, thread_sensitive=True)(
        principal=principal,
        limit=max(1, min(int(limit), 25)),
        expiring_days=max(1, min(int(expiring_days), 365)),
        structural_location_id=str(structural_location_id).strip() if structural_location_id else None,
    )


@mcp.tool(
    name="get_reorder_candidates",
    description="Return the out-of-stock and needs-reorder items that need replenishment first.",
)
async def get_reorder_candidates(
    limit: int = 10,
    structural_location_id: str | None = None,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_reorder_candidates_sync, thread_sensitive=True)(
        principal=principal,
        limit=max(1, min(int(limit), 25)),
        structural_location_id=str(structural_location_id).strip() if structural_location_id else None,
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
    name="list_stock_locations",
    description="List stock locations for the current tenant so the agent can present deterministic selectable options.",
)
async def list_stock_locations(
    limit: int = 25,
    structural_only: bool | None = None,
    external_only: bool | None = None,
) -> stock_payloads.StockLocationCollectionResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 50))
    return await sync_to_async(_search_stock_locations_sync, thread_sensitive=True)(
        principal=principal,
        query=None,
        limit=limit_value,
        structural_only=structural_only,
        external_only=external_only,
    )


@mcp.tool(
    name="list_stock_location_types",
    description="List available stock location types so the agent can resolve human labels to backend IDs.",
)
async def list_stock_location_types(
    query: str | None = None,
    limit: int = 25,
) -> stock_payloads.StockLocationTypeCollectionResponsePayload:
    limit_value = max(1, min(int(limit), 50))
    return await sync_to_async(_list_stock_location_types_sync, thread_sensitive=True)(
        query=query,
        limit=limit_value,
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
    name="search_stock_lots",
    description="Search stock lots by lot number, inventory item, or lifecycle status.",
)
async def search_stock_lots(
    query: str | None = None,
    limit: int = 10,
    inventory_item_id: str | None = None,
    status: str | None = None,
) -> stock_payloads.StockLotCollectionResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    return await sync_to_async(_search_stock_lots_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        limit=limit_value,
        inventory_item_id=str(inventory_item_id).strip() if inventory_item_id else None,
        status=status,
    )


@mcp.tool(
    name="search_stock_serials",
    description="Search stock serials by serial number, item, lot, location, or lifecycle status.",
)
async def search_stock_serials(
    query: str | None = None,
    limit: int = 10,
    inventory_item_id: str | None = None,
    status: str | None = None,
) -> stock_payloads.StockSerialCollectionResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    return await sync_to_async(_search_stock_serials_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        limit=limit_value,
        inventory_item_id=str(inventory_item_id).strip() if inventory_item_id else None,
        status=status,
    )


@mcp.tool(
    name="search_stock_balances",
    description="Search location-level stock balances by inventory item, location, or lot.",
)
async def search_stock_balances(
    query: str | None = None,
    limit: int = 10,
    inventory_item_id: str | None = None,
    location_id: str | None = None,
    structural_location_id: str | None = None,
) -> stock_payloads.StockBalanceCollectionResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 25))
    return await sync_to_async(_search_stock_balances_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        limit=limit_value,
        inventory_item_id=str(inventory_item_id).strip() if inventory_item_id else None,
        location_id=str(location_id).strip() if location_id else None,
        structural_location_id=str(structural_location_id).strip() if structural_location_id else None,
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
    date_from: str | None = None,
    date_to: str | None = None,
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
        date_from=str(date_from).strip() if date_from else None,
        date_to=str(date_to).strip() if date_to else None,
    )


@mcp.tool(
    name="get_stock_movements",
    description="Return recent stock movements using the same filters as stock-movement search.",
)
async def get_stock_movements(
    query: str | None = None,
    limit: int = 10,
    movement_type: str | None = None,
    inventory_item_id: str | None = None,
    reference_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> stock_payloads.StockMovementCollectionResponsePayload:
    return await search_stock_movements(
        query=query,
        limit=limit,
        movement_type=movement_type,
        inventory_item_id=inventory_item_id,
        reference_id=reference_id,
        date_from=date_from,
        date_to=date_to,
    )


@mcp.tool(
    name="get_stock_analytics",
    description="Get workspace-level stock analytics across locations, value, and aging posture.",
)
async def get_stock_analytics(
    structural_location_id: str | None = None,
) -> inventory_payloads.InventoryAnalyticsResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_stock_analytics_sync, thread_sensitive=True)(
        principal=principal,
        structural_location_id=str(structural_location_id).strip() if structural_location_id else None,
    )


@mcp.tool(
    name="search_purchase_orders",
    description="Search purchase orders for the authenticated workspace by reference, supplier, or status.",
)
async def search_purchase_orders(
    query: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10,
) -> orders_payloads.PurchaseOrderSearchResponsePayload:
    principal = get_current_principal(required=True)
    limit_value = max(1, min(int(limit), 50))
    return await sync_to_async(_search_purchase_orders_sync, thread_sensitive=True)(
        principal=principal,
        query=query,
        status=status,
        limit=limit_value,
        date_from=str(date_from).strip() if date_from else None,
        date_to=str(date_to).strip() if date_to else None,
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
async def get_purchase_order_analytics(
    date_from: str | None = None,
    date_to: str | None = None,
) -> orders_payloads.PurchaseOrderAnalyticsResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_purchase_order_analytics_sync, thread_sensitive=True)(
        principal=principal,
        date_from=str(date_from).strip() if date_from else None,
        date_to=str(date_to).strip() if date_to else None,
    )


@mcp.tool(
    name="get_po_pipeline",
    description="Return purchase-order pipeline counts and the most recent purchase orders.",
)
async def get_po_pipeline(
    limit: int = 20,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_po_pipeline_sync, thread_sensitive=True)(
        principal=principal,
        limit=max(1, min(int(limit), 50)),
    )


@mcp.tool(
    name="get_receiving_exceptions",
    description="Return open receiving exceptions across approved, issued, overdue, or partially received purchase orders.",
)
async def get_receiving_exceptions(
    limit: int = 20,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    return await sync_to_async(_get_receiving_exceptions_sync, thread_sensitive=True)(
        principal=principal,
        limit=max(1, min(int(limit), 50)),
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
    name="adjust_inventory_item_stock",
    description="Adjust stock on an inventory item. Payload should match the backend adjust_stock action schema.",
)
async def adjust_inventory_item_stock(
    inventory_item_id: str,
    payload: stock_payloads.InventoryAdjustmentRequestPayload,
) -> stock_payloads.StockAdjustmentResultPayload:
    principal = get_current_principal(required=True)
    target_id = str(inventory_item_id or "").strip()
    if not target_id:
        raise ValueError("inventory_item_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_adjust_inventory_item_stock_via_view_sync, thread_sensitive=True)(
        principal=principal,
        inventory_item_id=target_id,
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
    description="Get inventory items attached to an inventory category.",
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
        action="items",
        method="get",
        category_id=target_id,
    )


@mcp.tool(
    name="auto_categorize_inventory_items",
    description=(
        "Automatically categorize inventory items for the authenticated workspace. "
        "Use this when the user asks to categorize inventory items. By default it only "
        "updates uncategorized items, creates missing non-structural categories when "
        "there is a confident match, attaches matching items, and returns uncertain "
        "items for manual review."
    ),
)
async def auto_categorize_inventory_items(
    only_uncategorized: bool = True,
    create_missing_categories: bool = True,
    apply_changes: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    return await sync_to_async(_auto_categorize_inventory_items_sync, thread_sensitive=True)(
        principal=principal,
        only_uncategorized=only_uncategorized,
        create_missing_categories=create_missing_categories,
        apply_changes=apply_changes,
        limit=limit,
    )


@mcp.tool(
    name="recommend_inventory_item_controls",
    description=(
        "Recommend operational stock controls for an inventory item: minimum stock, "
        "safety stock, reorder point, reorder quantity, lot/serial/expiry tracking, "
        "and negative-stock policy. Use before creating inventory items when these "
        "fields are missing or zero, and use for existing items when the user asks "
        "to review or fix inventory item settings. If inventory_item_id is provided "
        "and apply_changes is true, only applies recommendations when all current "
        "replenishment thresholds are zero."
    ),
)
async def recommend_inventory_item_controls(
    inventory_item_id: str | None = None,
    item_name: str = "",
    category_name: str = "",
    description: str = "",
    apply_changes: bool = False,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    return await sync_to_async(_recommend_inventory_item_controls_sync, thread_sensitive=True)(
        principal=principal,
        inventory_item_id=inventory_item_id,
        item_name=item_name,
        category_name=category_name,
        description=description,
        apply_changes=apply_changes,
    )


@mcp.tool(
    name="assign_inventory_item_category",
    description=(
        "Assign an existing inventory item to an existing non-structural inventory category "
        "for the authenticated workspace. Use this instead of update_inventory_item when the "
        "only intended change is category assignment. The response includes assigned=true only "
        "after the saved item is re-read and verified."
    ),
)
async def assign_inventory_item_category(
    inventory_item_id: str,
    category_id: str,
) -> dict[str, Any]:
    principal = get_current_principal(required=True)
    target_item_id = str(inventory_item_id or "").strip()
    target_category_id = str(category_id or "").strip()
    if not target_item_id:
        raise ValueError("inventory_item_id is required")
    if not target_category_id:
        raise ValueError("category_id is required")
    return await sync_to_async(_assign_inventory_item_category_sync, thread_sensitive=True)(
        principal=principal,
        inventory_item_id=target_item_id,
        category_id=target_category_id,
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
    name="create_inventory_item",
    description="Create an inventory item definition. Payload should match the backend create schema.",
)
async def create_inventory_item(
    payload: inventory_payloads.InventoryItemCreatePayload,
) -> inventory_payloads.InventoryItemMutationResponsePayload:
    #  we need to properly define all payload fields and validation for this tool before we can safely expose it, as it has significant potential to cause data integrity issues if used incorrectly. For now, we'll leave this as a passthrough to the view action and require internal access until we can build out a more robust interface for inventory creation.
    principal = get_current_principal(required=True)
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_inventory_item_crud_action_sync, thread_sensitive=True)(
        principal=principal,
        action="create",
        method="post",
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="update_inventory_item",
    description="Update an inventory item definition. Payload should match the backend partial-update schema.",
)
async def update_inventory_item(
    inventory_item_id: str,
    payload: inventory_payloads.InventoryItemUpdatePayload,
) -> inventory_payloads.InventoryItemMutationResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(inventory_item_id or "").strip()
    if not target_id:
        raise ValueError("inventory_item_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_inventory_item_crud_action_sync, thread_sensitive=True)(
        principal=principal,
        action="partial_update",
        method="patch",
        inventory_item_id=target_id,
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="bulk_update_inventory_item_controls",
    description="Bulk update stock-control thresholds and tracking flags across inventory items.",
)
async def bulk_update_inventory_item_controls(
    payload: inventory_payloads.BulkInventoryItemControlsPayload,
) -> inventory_payloads.BulkInventoryItemControlsResponsePayload:
    principal = get_current_principal(required=True)
    if not payload or not payload.updates:
        raise ValueError("payload.updates is required")
    return await sync_to_async(_bulk_update_inventory_item_controls_sync, thread_sensitive=True)(
        principal=principal,
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
    name="get_inventory_item_tracking_history",
    description="Get the full movement history for an inventory item.",
)
async def get_inventory_item_tracking_history(
    inventory_item_id: str,
) -> stock_payloads.InventoryItemActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(inventory_item_id or "").strip()
    if not target_id:
        raise ValueError("inventory_item_id is required")
    return await sync_to_async(_inventory_item_action_sync, thread_sensitive=True)(
        principal=principal,
        action="tracking_history",
        method="get",
        inventory_item_id=target_id,
    )


@mcp.tool(
    name="update_inventory_item_status",
    description="Update the lifecycle status of an inventory item.",
)
async def update_inventory_item_status(
    inventory_item_id: str,
    payload: stock_payloads.StockStatusUpdatePayload,
) -> stock_payloads.InventoryItemActionResponsePayload:
    principal = get_current_principal(required=True)
    target_id = str(inventory_item_id or "").strip()
    if not target_id:
        raise ValueError("inventory_item_id is required")
    if not payload:
        raise ValueError("payload is required")
    return await sync_to_async(_inventory_item_action_sync, thread_sensitive=True)(
        principal=principal,
        action="update_status",
        method="post",
        inventory_item_id=target_id,
        data=_payload_to_data(payload),
    )


@mcp.tool(
    name="search_expiring_inventory_items",
    description="List inventory items that are expiring soon.",
)
async def search_expiring_inventory_items(
    days: int = 30,
) -> stock_payloads.InventoryItemActionResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_inventory_item_action_sync, thread_sensitive=True)(
        principal=principal,
        action="expiring_soon",
        method="get",
        query_params={"days": max(1, min(int(days), 365))},
    )


@mcp.tool(
    name="search_low_inventory_items",
    description="List low-stock inventory items from the stock service dashboard view.",
)
async def search_low_inventory_items() -> stock_payloads.InventoryItemActionResponsePayload:
    principal = get_current_principal(required=True)
    return await sync_to_async(_inventory_item_action_sync, thread_sensitive=True)(
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
