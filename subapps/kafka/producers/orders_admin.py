from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from mainapps.identity.models import IdentityCompanyProfile, IdentityUser
from mainapps.orders.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLineItem,
    ReturnOrder,
    ReturnOrderLineItem,
    SalesOrder,
    SalesOrderLineItem,
    SalesOrderShipment,
)
from subapps.kafka.producers.platform_events import publish_audit_fact, publish_workspace_notification
from subapps.utils.request_context import current_frontend_origin


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


def _json_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _string(value)


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
            payload.get("shipped_by_user_id"),
            payload.get("actor_user_id"),
        ]
    )
    owner_user_id = _workspace_owner_user_id(workspace_id)
    if owner_user_id:
        candidate_user_ids.append(owner_user_id)

    user_ids = _string_list(candidate_user_ids)
    affected_users = [snapshot for snapshot in (_notification_user_snapshot(user_id) for user_id in user_ids) if snapshot]
    return user_ids, affected_users


def serialize_purchase_order(order: PurchaseOrder) -> dict[str, Any]:
    return {
        "purchase_order_id": str(order.id),
        "profile_id": order.profile_id,
        "reference": order.reference,
        "status": order.status,
        "workflow_state": order.workflow_state or "",
        "supplier_id": str(order.supplier_id or ""),
        "supplier_name": _string(getattr(order.supplier, "name", "")),
        "supplier_reference": order.supplier_reference or "",
        "description": order.description or "",
        "notes": order.notes or "",
        "issue_date": _json_date(order.issue_date),
        "delivery_date": _json_date(order.delivery_date),
        "received_date": _json_date(order.received_date),
        "approved_at": _json_date(order.approved_at),
        "total_price": _json_decimal(order.total_price),
        "created_by_user_id": order.created_by_user_id,
        "updated_by_user_id": order.updated_by_user_id,
        "approved_by_user_id": order.approved_by_user_id,
        "received_by_user_id": order.received_by_user_id,
        "responsible_user_id": order.responsible_user_id,
    }


def serialize_purchase_order_line_item(line_item: PurchaseOrderLineItem) -> dict[str, Any]:
    inventory_item = line_item.inventory_item
    return {
        "purchase_order_line_item_id": str(line_item.id),
        "purchase_order_id": str(line_item.purchase_order_id),
        "purchase_order_reference": _string(getattr(line_item.purchase_order, "reference", "")),
        "profile_id": getattr(line_item.purchase_order, "profile_id", None),
        "inventory_item_id": str(line_item.inventory_item_id),
        "inventory_name": inventory_item.name_snapshot,
        "inventory_sku": inventory_item.sku_snapshot or "",
        "inventory_barcode": inventory_item.barcode_snapshot or "",
        "product_variant_image_url": inventory_item.product_variant_image_url or "",
        "display_image": inventory_item.product_variant_image_url or "",
        "quantity": _json_decimal(line_item.quantity),
        "quantity_received": _json_decimal(line_item.quantity_received),
        "remaining_quantity": _json_decimal(line_item.remaining_quantity),
        "unit_price": _json_decimal(line_item.unit_price),
        "tax_rate": _json_decimal(line_item.tax_rate),
        "discount_rate": _json_decimal(line_item.discount_rate),
        "batch_number": line_item.batch_number or "",
        "expiry_date": _json_date(line_item.expiry_date),
        "manufactured_date": _json_date(line_item.manufactured_date),
        "fully_received": line_item.fully_received,
        "created_by_user_id": line_item.created_by_user_id,
        "updated_by_user_id": line_item.updated_by_user_id,
    }


def serialize_goods_receipt(goods_receipt: GoodsReceipt) -> dict[str, Any]:
    return {
        "goods_receipt_id": str(goods_receipt.id),
        "profile_id": goods_receipt.profile_id,
        "reference": goods_receipt.reference,
        "purchase_order_id": str(goods_receipt.purchase_order_id or ""),
        "purchase_order_reference": _string(getattr(goods_receipt.purchase_order, "reference", "")),
        "supplier_id": str(goods_receipt.supplier_id or ""),
        "supplier_name": _string(getattr(goods_receipt.supplier, "name", "")),
        "received_at": _json_date(goods_receipt.received_at),
        "received_by_user_id": goods_receipt.received_by_user_id,
        "notes": goods_receipt.notes or "",
    }


def serialize_goods_receipt_line(goods_receipt_line: GoodsReceiptLine) -> dict[str, Any]:
    inventory_item = goods_receipt_line.inventory_item
    purchase_order_line = goods_receipt_line.purchase_order_line
    goods_receipt = goods_receipt_line.goods_receipt
    purchase_order = getattr(goods_receipt, "purchase_order", None)
    stock_location = goods_receipt_line.stock_location
    return {
        "goods_receipt_line_id": str(goods_receipt_line.id),
        "goods_receipt_id": str(goods_receipt_line.goods_receipt_id),
        "goods_receipt_reference": _string(getattr(goods_receipt, "reference", "")),
        "purchase_order_id": str(getattr(goods_receipt, "purchase_order_id", "") or ""),
        "purchase_order_reference": _string(getattr(purchase_order, "reference", "")),
        "purchase_order_line_item_id": str(getattr(goods_receipt_line, "purchase_order_line_id", "") or ""),
        "profile_id": getattr(goods_receipt, "profile_id", None),
        "inventory_item_id": str(goods_receipt_line.inventory_item_id),
        "inventory_name": inventory_item.name_snapshot,
        "inventory_sku": inventory_item.sku_snapshot or "",
        "inventory_barcode": inventory_item.barcode_snapshot or "",
        "product_variant_image_url": inventory_item.product_variant_image_url or "",
        "display_image": inventory_item.product_variant_image_url or "",
        "stock_location_id": str(goods_receipt_line.stock_location_id),
        "stock_location_name": _string(getattr(stock_location, "name", "")),
        "received_quantity": _json_decimal(goods_receipt_line.received_quantity),
        "unit_cost": _json_decimal(goods_receipt_line.unit_cost),
        "lot_number": goods_receipt_line.lot_number or "",
        "manufactured_date": _json_date(goods_receipt_line.manufactured_date),
        "expiry_date": _json_date(goods_receipt_line.expiry_date),
        "quantity_received_to_date": _json_decimal(getattr(purchase_order_line, "quantity_received", None)),
        "remaining_quantity": _json_decimal(getattr(purchase_order_line, "remaining_quantity", None)),
        "fully_received": bool(getattr(purchase_order_line, "fully_received", False)),
        "created_by_user_id": goods_receipt_line.created_by_user_id,
        "updated_by_user_id": goods_receipt_line.updated_by_user_id,
    }


def serialize_sales_order(order: SalesOrder) -> dict[str, Any]:
    return {
        "sales_order_id": str(order.id),
        "profile_id": order.profile_id,
        "reference": order.reference,
        "status": order.status,
        "customer_id": str(order.customer_id or ""),
        "customer_name": _string(getattr(order.customer, "name", "")),
        "customer_reference": order.customer_reference or "",
        "description": order.description or "",
        "notes": order.notes or "",
        "issue_date": _json_date(order.issue_date),
        "shipment_date": _json_date(order.shipment_date),
        "delivery_date": _json_date(order.delivery_date),
        "complete_date": _json_date(order.complete_date),
        "total_price": _json_decimal(order.total_price),
        "created_by_user_id": order.created_by_user_id,
        "updated_by_user_id": order.updated_by_user_id,
        "shipped_by_user_id": order.shipped_by_user_id,
        "responsible_user_id": order.responsible_user_id,
    }


def serialize_sales_order_line_item(line_item: SalesOrderLineItem) -> dict[str, Any]:
    inventory_item = line_item.inventory_item
    return {
        "sales_order_line_item_id": str(line_item.id),
        "sales_order_id": str(line_item.sales_order_id),
        "sales_order_reference": _string(getattr(line_item.sales_order, "reference", "")),
        "profile_id": getattr(line_item.sales_order, "profile_id", None),
        "inventory_item_id": str(line_item.inventory_item_id),
        "inventory_name": inventory_item.name_snapshot,
        "inventory_sku": inventory_item.sku_snapshot or "",
        "inventory_barcode": inventory_item.barcode_snapshot or "",
        "quantity": _json_decimal(line_item.quantity),
        "reserved_quantity": _json_decimal(line_item.reserved_quantity),
        "shipped_quantity": _json_decimal(line_item.shipped_quantity),
        "remaining_quantity": _json_decimal(line_item.remaining_quantity),
        "reservable_quantity": _json_decimal(line_item.reservable_quantity),
        "unit_price": _json_decimal(line_item.unit_price),
        "tax_rate": _json_decimal(line_item.tax_rate),
        "discount_rate": _json_decimal(line_item.discount_rate),
        "created_by_user_id": line_item.created_by_user_id,
        "updated_by_user_id": line_item.updated_by_user_id,
    }


def serialize_sales_order_shipment(shipment: SalesOrderShipment) -> dict[str, Any]:
    return {
        "sales_order_shipment_id": str(shipment.id),
        "profile_id": shipment.profile_id,
        "reference": shipment.reference,
        "sales_order_id": str(shipment.order_id),
        "sales_order_reference": _string(getattr(shipment.order, "reference", "")),
        "shipment_date": _json_date(shipment.shipment_date),
        "delivery_date": _json_date(shipment.delivery_date),
        "tracking_number": shipment.tracking_number or "",
        "invoice_number": shipment.invoice_number or "",
        "notes": shipment.notes or "",
        "checked_by_user_id": shipment.checked_by_user_id,
        "created_by_user_id": shipment.created_by_user_id,
        "updated_by_user_id": shipment.updated_by_user_id,
    }


def serialize_return_order(return_order: ReturnOrder) -> dict[str, Any]:
    return {
        "return_order_id": str(return_order.id),
        "profile_id": return_order.profile_id,
        "reference": return_order.reference,
        "status": return_order.status,
        "purchase_order_id": str(return_order.purchase_order_id or ""),
        "purchase_order_reference": _string(getattr(return_order.purchase_order, "reference", "")),
        "customer_id": str(return_order.customer_id or ""),
        "customer_name": _string(getattr(return_order.customer, "name", "")),
        "return_reason": return_order.return_reason or "",
        "issue_date": _json_date(return_order.issue_date),
        "complete_date": _json_date(return_order.complete_date),
        "created_by_user_id": return_order.created_by_user_id,
        "updated_by_user_id": return_order.updated_by_user_id,
        "responsible_user_id": return_order.responsible_user_id,
    }


def serialize_return_order_line_item(line_item: ReturnOrderLineItem) -> dict[str, Any]:
    original_line = line_item.original_line_item
    inventory_item = original_line.inventory_item
    return {
        "return_order_line_item_id": str(line_item.id),
        "return_order_id": str(line_item.return_order_id),
        "return_order_reference": _string(getattr(line_item.return_order, "reference", "")),
        "profile_id": getattr(line_item.return_order, "profile_id", None),
        "purchase_order_line_item_id": str(line_item.original_line_item_id),
        "inventory_item_id": str(original_line.inventory_item_id),
        "inventory_name": inventory_item.name_snapshot,
        "inventory_sku": inventory_item.sku_snapshot or "",
        "inventory_barcode": inventory_item.barcode_snapshot or "",
        "quantity_returned": _json_decimal(line_item.quantity_returned),
        "quantity_processed": _json_decimal(line_item.quantity_processed),
        "remaining_quantity": _json_decimal(line_item.remaining_quantity),
        "unit_price": _json_decimal(line_item.unit_price),
        "tax_rate": _json_decimal(line_item.tax_rate),
        "discount": _json_decimal(line_item.discount),
        "return_reason": line_item.return_reason or "",
        "created_by_user_id": line_item.created_by_user_id,
        "updated_by_user_id": line_item.updated_by_user_id,
    }


def publish_order_admin_event(
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
    feature_area: str = "order_management",
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
            notification_metadata.setdefault("reference_number", reference_number or _string(payload.get("reference")))
            notification_metadata.setdefault("event_name", event_name)
            publish_workspace_notification(
                event_name=f"notification.{event_name}",
                workspace_id=_string(payload.get("profile_id")),
                category=notification_category,
                title=notification_title,
                message=notification_message,
                metadata={**payload, **notification_metadata},
                action_url=notification_action_url,
                frontend_origin=payload.get("frontend_origin") or current_frontend_origin(),
                user_ids=recipients,
                key=f"{payload.get('profile_id')}:{target.get('type')}:{target.get('id')}:{event_name}:notification",
            )
    return event
