from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from mainapps.identity.models import IdentityCompanyProfile, IdentityUser
from mainapps.inventory.models import InventoryCategory, InventoryItem
from mainapps.stock.models import StockLocation, StockReservation
from subapps.kafka.producers.platform_events import publish_audit_fact, publish_workspace_notification


def _string(value: Any) -> str:
    return str(value or "").strip()


def _json_decimal(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_list(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = _string(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _notification_user_snapshot(user_id: Any) -> dict[str, Any] | None:
    normalized_user_id = _string(user_id)
    if not normalized_user_id:
        return None
    snapshot: dict[str, Any] = {"user_id": normalized_user_id}
    try:
        user = IdentityUser.objects.filter(user_id=int(normalized_user_id), is_active=True).first()
    except (TypeError, ValueError):
        user = None
    if user is not None:
        snapshot["user_email"] = _string(user.email)
        snapshot["user_name"] = _string(user.full_name)
        snapshot["is_active"] = bool(user.is_active)
    return snapshot


def _workspace_owner_user_id(workspace_id: Any) -> str:
    normalized_workspace_id = _string(workspace_id)
    if not normalized_workspace_id:
        return ""
    try:
        profile = IdentityCompanyProfile.objects.filter(profile_id=int(normalized_workspace_id), is_active=True).only("owner_user_id").first()
    except (TypeError, ValueError):
        profile = None
    return _string(getattr(profile, "owner_user_id", ""))


def _notification_recipients(
    *,
    workspace_id: Any,
    payload: dict[str, Any],
    explicit_user_ids: Iterable[Any] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    candidate_user_ids: list[Any] = []
    candidate_user_ids.extend(list(explicit_user_ids or []))
    candidate_user_ids.extend(
        [
            payload.get("responsible_user_id"),
            payload.get("created_by_user_id"),
            payload.get("updated_by_user_id"),
            payload.get("approved_by_user_id"),
            payload.get("received_by_user_id"),
            payload.get("actor_user_id"),
            payload.get("official_user_id"),
        ]
    )
    owner_user_id = _workspace_owner_user_id(workspace_id)
    if owner_user_id:
        candidate_user_ids.append(owner_user_id)

    user_ids = _string_list(candidate_user_ids)
    affected_users = [snapshot for snapshot in (_notification_user_snapshot(user_id) for user_id in user_ids) if snapshot]
    return user_ids, affected_users


def serialize_inventory_category(category: InventoryCategory) -> dict[str, Any]:
    return {
        "inventory_category_id": str(category.id),
        "profile_id": category.profile_id,
        "name": category.name,
        "slug": category.slug,
        "description": category.description or "",
        "is_active": category.is_active,
        "structural": category.structural,
        "parent_id": str(category.parent_id or ""),
        "parent_name": _string(getattr(category.parent, "name", "")),
        "default_location_id": str(category.default_location_id or ""),
        "default_location_name": _string(getattr(category.default_location, "name", "")),
        "created_by_user_id": category.created_by_user_id,
        "updated_by_user_id": category.updated_by_user_id,
    }


def serialize_inventory_item(item: InventoryItem) -> dict[str, Any]:
    return {
        "inventory_item_id": str(item.id),
        "profile_id": item.profile_id,
        "name_snapshot": item.name_snapshot,
        "sku_snapshot": item.sku_snapshot or "",
        "barcode_snapshot": item.barcode_snapshot or "",
        "inventory_category_id": str(item.inventory_category_id or ""),
        "inventory_category_name": _string(getattr(item.inventory_category, "name", "")),
        "inventory_type": item.inventory_type,
        "default_supplier_id": str(item.default_supplier_id or ""),
        "default_supplier_name": _string(getattr(item.default_supplier, "name", "")),
        "default_uom_code": item.default_uom_code or "",
        "stock_uom_code": item.stock_uom_code or "",
        "track_stock": item.track_stock,
        "track_lot": item.track_lot,
        "track_serial": item.track_serial,
        "track_expiry": item.track_expiry,
        "allow_negative_stock": item.allow_negative_stock,
        "reorder_point": _json_decimal(item.reorder_point),
        "reorder_quantity": _json_decimal(item.reorder_quantity),
        "minimum_stock_level": _json_decimal(item.minimum_stock_level),
        "safety_stock_level": _json_decimal(item.safety_stock_level),
        "status": item.status,
        "product_template_id": str(item.product_template_id or ""),
        "product_variant_id": str(item.product_variant_id or ""),
        "created_by_user_id": item.created_by_user_id,
        "updated_by_user_id": item.updated_by_user_id,
    }


def serialize_stock_location(location: StockLocation) -> dict[str, Any]:
    return {
        "stock_location_id": str(location.id),
        "profile_id": location.profile_id,
        "name": location.name or "",
        "code": location.code or "",
        "description": location.description or "",
        "structural": location.structural,
        "external": location.external,
        "is_default_structural_location": location.is_default_structural_location,
        "parent_id": str(location.parent_id or ""),
        "parent_name": _string(getattr(location.parent, "name", "")),
        "location_type_id": str(location.location_type_id or ""),
        "location_type_name": _string(getattr(location.location_type, "name", "")),
        "official_user_id": location.official_user_id,
        "physical_address": location.physical_address or "",
        "created_by_user_id": location.created_by_user_id,
        "updated_by_user_id": location.updated_by_user_id,
    }


def serialize_stock_reservation(reservation: StockReservation) -> dict[str, Any]:
    inventory_item = reservation.inventory_item
    stock_location = reservation.stock_location
    stock_lot = reservation.stock_lot
    stock_serial = reservation.stock_serial
    return {
        "stock_reservation_id": str(reservation.id),
        "profile_id": reservation.profile_id,
        "inventory_item_id": str(reservation.inventory_item_id),
        "inventory_name": inventory_item.name_snapshot,
        "inventory_sku": inventory_item.sku_snapshot or "",
        "inventory_barcode": inventory_item.barcode_snapshot or "",
        "product_variant_image_url": inventory_item.product_variant_image_url or "",
        "display_image": inventory_item.product_variant_image_url or "",
        "stock_location_id": str(reservation.stock_location_id),
        "stock_location_name": _string(getattr(stock_location, "name", "")),
        "stock_lot_id": str(reservation.stock_lot_id or ""),
        "lot_number": _string(getattr(stock_lot, "lot_number", "")),
        "stock_serial_id": str(reservation.stock_serial_id or ""),
        "serial_number": _string(getattr(stock_serial, "serial_number", "")),
        "external_order_type": reservation.external_order_type,
        "external_order_id": reservation.external_order_id,
        "external_order_line_id": reservation.external_order_line_id or "",
        "reserved_quantity": _json_decimal(reservation.reserved_quantity),
        "fulfilled_quantity": _json_decimal(reservation.fulfilled_quantity),
        "remaining_quantity": _json_decimal(reservation.remaining_quantity),
        "status": reservation.status,
        "expires_at": reservation.expires_at.isoformat() if reservation.expires_at else "",
        "created_by_user_id": reservation.created_by_user_id,
        "updated_by_user_id": reservation.updated_by_user_id,
    }


def publish_inventory_admin_event(
    *,
    event_name: str,
    payload: dict[str, Any],
    actor: dict[str, Any],
    target: dict[str, Any],
    summary: str,
    metadata: dict[str, Any] | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    severity: str = "info",
    feature_area: str = "inventory",
    reference_number: str = "",
    notification_category: str = "",
    notification_title: str = "",
    notification_message: str = "",
    notification_action_url: str | None = None,
    notification_user_ids: Iterable[Any] | None = None,
) -> dict[str, Any]:
    event = publish_audit_fact(
        event_name=event_name,
        payload=payload,
        workspace_id=_string(payload.get("profile_id")),
        actor=actor,
        target=target,
        summary=summary,
        severity=severity,
        metadata=metadata or {},
        changes={"before": before or {}, "after": after or payload},
        feature_area=feature_area,
        reference_number=reference_number,
        key=f"{payload.get('profile_id')}:{target.get('type')}:{target.get('id')}:{event_name}",
    )
    if notification_category and notification_title and notification_message:
        recipients, affected_users = _notification_recipients(
            workspace_id=payload.get("profile_id"),
            payload=payload,
            explicit_user_ids=notification_user_ids,
        )
        if recipients:
            notification_metadata = dict(metadata or {})
            notification_metadata.setdefault("affected_users", affected_users)
            notification_metadata.setdefault("target", target)
            notification_metadata.setdefault("reference_number", reference_number)
            notification_metadata.setdefault("event_name", event_name)
            publish_workspace_notification(
                event_name=f"notification.{event_name}",
                workspace_id=_string(payload.get("profile_id")),
                category=notification_category,
                title=notification_title,
                message=notification_message,
                metadata={**payload, **notification_metadata},
                action_url=notification_action_url,
                user_ids=recipients,
                key=f"{payload.get('profile_id')}:{target.get('type')}:{target.get('id')}:{event_name}:notification",
            )
    return event
