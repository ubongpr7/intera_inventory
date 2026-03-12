from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone

from mainapps.inventory.models import Inventory, InventoryItem
from mainapps.orders.models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLineItem
from mainapps.stock.models import (
    StockBalance,
    StockItem,
    StockLocation,
    StockLot,
    StockSerial,
    StockSerialStatus,
    StockMovement,
    StockMovementType,
    StockReservation,
    StockReservationStatus,
    TrackingType,
)


class StockDomainError(ValueError):
    pass


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _coerce_profile_id(value):
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_whole_number(value, *, label: str) -> int:
    quantity = _to_decimal(value)
    if quantity != quantity.to_integral_value():
        raise StockDomainError(f"{label} must be a whole number for serial-tracked inventory.")
    return int(quantity)


def _normalize_serial_numbers(serial_numbers) -> list[str]:
    if not serial_numbers:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in serial_numbers:
        serial_number = str(raw_value or "").strip()
        if not serial_number:
            raise StockDomainError("Serial numbers cannot be blank.")
        if serial_number in seen:
            raise StockDomainError(f"Duplicate serial number '{serial_number}' provided.")
        seen.add(serial_number)
        normalized.append(serial_number)
    return normalized


class StockDomainService:
    @classmethod
    def create_goods_receipt(
        cls,
        *,
        purchase_order: PurchaseOrder,
        actor_user_id=None,
        notes: str = "",
    ) -> GoodsReceipt:
        profile_id = cls._resolve_profile_id(purchase_order)
        return GoodsReceipt.objects.create(
            profile_id=profile_id,
            purchase_order=purchase_order,
            supplier=purchase_order.supplier,
            received_at=timezone.now(),
            received_by_user_id=actor_user_id,
            notes=notes or "",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

    @classmethod
    @transaction.atomic
    def receive_purchase_line(
        cls,
        *,
        purchase_order: PurchaseOrder,
        line_item: PurchaseOrderLineItem,
        stock_location: StockLocation,
        quantity_received,
        actor_user_id=None,
        goods_receipt: GoodsReceipt | None = None,
        lot_number: str = "",
        manufactured_date=None,
        expiry_date=None,
        serial_numbers=None,
        notes: str = "",
    ):
        quantity_received = _to_decimal(quantity_received)
        if quantity_received <= 0:
            raise StockDomainError("Received quantity must be greater than zero.")

        if line_item.quantity_received + quantity_received > line_item.quantity:
            raise StockDomainError(
                f"Cannot receive {quantity_received}; only {line_item.remaining_quantity} remains open."
            )

        profile_id = cls._resolve_profile_id(purchase_order)
        inventory_item = cls.ensure_inventory_item(
            purchase_order_line=line_item,
            actor_user_id=actor_user_id,
        )

        if goods_receipt is None:
            goods_receipt = cls.create_goods_receipt(
                purchase_order=purchase_order,
                actor_user_id=actor_user_id,
                notes=notes,
            )

        line_lot_number = lot_number or line_item.batch_number or ""
        line_manufactured_date = manufactured_date or line_item.manufactured_date
        line_expiry_date = expiry_date or line_item.expiry_date
        received_serial_numbers = _normalize_serial_numbers(serial_numbers)

        if inventory_item.track_serial:
            serial_count = _to_whole_number(quantity_received, label="Received quantity")
            if len(received_serial_numbers) != serial_count:
                raise StockDomainError(
                    "Serial-tracked inventory requires exactly one serial number for each received unit."
                )
        elif received_serial_numbers:
            raise StockDomainError("Serial numbers were provided for an inventory item that is not serial-tracked.")

        goods_receipt_line = GoodsReceiptLine.objects.create(
            goods_receipt=goods_receipt,
            purchase_order_line=line_item,
            inventory_item=inventory_item,
            stock_location=stock_location,
            received_quantity=quantity_received,
            unit_cost=line_item.unit_price,
            lot_number=line_lot_number,
            manufactured_date=line_manufactured_date,
            expiry_date=line_expiry_date,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        stock_lot = None
        if inventory_item.track_lot or line_lot_number or line_expiry_date or line_manufactured_date:
            stock_lot = StockLot.objects.create(
                profile_id=profile_id,
                inventory_item=inventory_item,
                supplier=purchase_order.supplier,
                purchase_order_line=line_item,
                goods_receipt_line=goods_receipt_line,
                lot_number=line_lot_number,
                manufactured_date=line_manufactured_date,
                expiry_date=line_expiry_date,
                unit_cost=line_item.unit_price,
                currency_code=purchase_order.order_currency or "",
                received_quantity=quantity_received,
                remaining_quantity=quantity_received,
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
            if not goods_receipt_line.lot_number and stock_lot.lot_number:
                goods_receipt_line.lot_number = stock_lot.lot_number
                goods_receipt_line.updated_by_user_id = actor_user_id
                goods_receipt_line.save()

        balance = cls._get_locked_balance(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_location,
            stock_lot=stock_lot,
            actor_user_id=actor_user_id,
        )
        balance.quantity_on_hand = _to_decimal(balance.quantity_on_hand) + quantity_received
        balance.updated_by_user_id = actor_user_id
        balance.save()

        stock_serials = cls._create_receipt_serials(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_location,
            stock_lot=stock_lot,
            serial_numbers=received_serial_numbers,
            actor_user_id=actor_user_id,
        )

        line_item.quantity_received = _to_decimal(line_item.quantity_received) + quantity_received
        line_item.updated_by_user_id = actor_user_id
        line_item.save()

        if stock_serials:
            for stock_serial in stock_serials:
                StockMovement.objects.create(
                    profile_id=profile_id,
                    inventory_item=inventory_item,
                    stock_lot=stock_lot,
                    stock_serial=stock_serial,
                    to_location=stock_location,
                    movement_type=StockMovementType.RECEIPT,
                    quantity=Decimal("1"),
                    unit_cost=line_item.unit_price,
                    reference_type="goods_receipt_line",
                    reference_id=str(goods_receipt_line.id),
                    actor_user_id=actor_user_id,
                    notes=notes or f"Received serial {stock_serial.serial_number} against PO {purchase_order.reference}",
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
        else:
            StockMovement.objects.create(
                profile_id=profile_id,
                inventory_item=inventory_item,
                stock_lot=stock_lot,
                to_location=stock_location,
                movement_type=StockMovementType.RECEIPT,
                quantity=quantity_received,
                unit_cost=line_item.unit_price,
                reference_type="goods_receipt_line",
                reference_id=str(goods_receipt_line.id),
                actor_user_id=actor_user_id,
                notes=notes or f"Received against PO {purchase_order.reference}",
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )

        cls._publish_inventory_availability_on_commit(inventory_item.id)
        return {
            "goods_receipt_line": goods_receipt_line,
            "stock_lot": stock_lot,
            "stock_serials": stock_serials,
            "balance": balance,
        }

    @classmethod
    @transaction.atomic
    def transfer_stock(
        cls,
        *,
        stock_item: StockItem | None = None,
        inventory_item: InventoryItem | None = None,
        from_location: StockLocation | None = None,
        to_location: StockLocation,
        quantity,
        actor_user_id=None,
        stock_lot: StockLot | None = None,
        stock_serial: StockSerial | None = None,
        serial_number: str = "",
        notes: str = "",
    ):
        quantity = _to_decimal(quantity)
        if quantity <= 0:
            raise StockDomainError("Transfer quantity must be greater than zero.")
        if stock_item is not None and from_location is None:
            from_location = stock_item.location
        if from_location is None:
            raise StockDomainError("Source location is required for stock transfer.")
        if from_location.id == to_location.id:
            raise StockDomainError("Source and destination locations must be different.")

        inventory_item, legacy_inventory, profile_id = cls._resolve_inventory_context(
            inventory_item=inventory_item,
            stock_item=stock_item,
            actor_user_id=actor_user_id,
        )
        if stock_lot is None and stock_item is not None:
            stock_lot = cls.resolve_stock_lot(stock_item=stock_item, inventory_item=inventory_item)

        if inventory_item.track_serial:
            transfer_count = _to_whole_number(quantity, label="Transfer quantity")
            if transfer_count != 1:
                raise StockDomainError("Serial-tracked inventory can only transfer one serial per operation.")
            stock_serial = cls._resolve_stock_serial(
                profile_id=profile_id,
                inventory_item=inventory_item,
                stock_location=from_location,
                stock_lot=stock_lot,
                stock_serial=stock_serial,
                serial_number=serial_number or (stock_item.serial if stock_item is not None else ""),
                allowed_statuses=[StockSerialStatus.AVAILABLE],
            )
            if stock_lot is None and stock_serial.stock_lot_id:
                stock_lot = stock_serial.stock_lot
        elif stock_serial is not None or serial_number:
            raise StockDomainError("Serial selection is only valid for serial-tracked inventory.")

        if stock_lot is None and inventory_item.track_lot:
            candidate_balance = (
                StockBalance.objects.select_for_update()
                .filter(
                    profile_id=profile_id,
                    inventory_item=inventory_item,
                    stock_location=from_location,
                    stock_lot__isnull=False,
                    quantity_available__gte=quantity,
                )
                .select_related('stock_lot')
                .order_by('stock_lot__expiry_date', 'created_at')
                .first()
            )
            if candidate_balance is None:
                raise StockDomainError(
                    "Lot-tracked inventory requires a stock lot with enough available quantity to transfer."
                )
            stock_lot = candidate_balance.stock_lot

        if stock_lot and stock_lot.inventory_item_id != inventory_item.id:
            raise StockDomainError("Stock lot does not belong to the selected inventory item.")

        source_balance = cls._get_locked_balance(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=from_location,
            stock_lot=stock_lot,
            legacy_inventory=legacy_inventory,
            actor_user_id=actor_user_id,
        )
        source_available = _to_decimal(source_balance.quantity_available)
        source_on_hand = _to_decimal(source_balance.quantity_on_hand)

        if source_available < quantity and not inventory_item.allow_negative_stock:
            raise StockDomainError("Insufficient stock quantity.")
        if source_on_hand - quantity < 0 and not inventory_item.allow_negative_stock:
            raise StockDomainError("Insufficient stock quantity.")

        source_balance.quantity_on_hand = source_on_hand - quantity
        source_balance.updated_by_user_id = actor_user_id
        source_balance.save()

        destination_balance = cls._get_locked_balance(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=to_location,
            stock_lot=stock_lot,
            legacy_inventory=legacy_inventory,
            actor_user_id=actor_user_id,
        )
        destination_balance.quantity_on_hand = _to_decimal(destination_balance.quantity_on_hand) + quantity
        destination_balance.updated_by_user_id = actor_user_id
        destination_balance.save()

        if stock_serial is not None:
            stock_serial.stock_location = to_location
            stock_serial.updated_by_user_id = actor_user_id
            stock_serial.save()

        StockMovement.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_lot=stock_lot,
            stock_serial=stock_serial,
            from_location=from_location,
            to_location=to_location,
            movement_type=StockMovementType.TRANSFER,
            quantity=quantity,
            unit_cost=cls._resolve_inventory_unit_cost(
                inventory_item=inventory_item,
                stock_lot=stock_lot,
                stock_location=from_location,
            ),
            reference_type="inventory_item",
            reference_id=str(inventory_item.id),
            actor_user_id=actor_user_id,
            notes=notes or f"Transferred from {from_location.name} to {to_location.name}",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        return {
            "inventory_item": inventory_item,
            "source_balance": source_balance,
            "destination_balance": destination_balance,
        }

    @classmethod
    @transaction.atomic
    def adjust_stock(
        cls,
        *,
        inventory: Inventory | None = None,
        inventory_item: InventoryItem | None = None,
        stock_location: StockLocation,
        quantity_change,
        actor_user_id=None,
        reason: str = "",
    ):
        quantity_change = _to_decimal(quantity_change)
        if quantity_change == 0:
            raise StockDomainError("Quantity change cannot be zero.")

        inventory_item, legacy_inventory, profile_id = cls._resolve_inventory_context(
            inventory=inventory,
            inventory_item=inventory_item,
            actor_user_id=actor_user_id,
        )

        balance = cls._get_locked_balance(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_location,
            legacy_inventory=legacy_inventory,
            actor_user_id=actor_user_id,
        )
        previous_quantity = _to_decimal(balance.quantity_on_hand)
        next_quantity = previous_quantity + quantity_change
        if next_quantity < 0 and not inventory_item.allow_negative_stock:
            raise StockDomainError("Insufficient stock quantity.")

        balance.quantity_on_hand = next_quantity
        balance.updated_by_user_id = actor_user_id
        balance.save()

        StockMovement.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            from_location=stock_location if quantity_change < 0 else None,
            to_location=stock_location if quantity_change > 0 else None,
            movement_type=StockMovementType.ADJUSTMENT,
            quantity=quantity_change,
            reference_type="inventory_item" if legacy_inventory is None else "inventory",
            reference_id=str(inventory_item.id if legacy_inventory is None else legacy_inventory.id),
            actor_user_id=actor_user_id,
            notes=reason or "Manual stock adjustment",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        cls._publish_inventory_availability_on_commit(inventory_item.id)
        return {
            "balance": balance,
            "old_quantity": previous_quantity,
            "new_quantity": next_quantity,
        }

    @classmethod
    def _resolve_inventory_context(
        cls,
        *,
        inventory: Inventory | None = None,
        inventory_item: InventoryItem | None = None,
        stock_item: StockItem | None = None,
        purchase_order_line: PurchaseOrderLineItem | None = None,
        actor_user_id=None,
    ) -> tuple[InventoryItem, Inventory | None, int]:
        resolved_inventory_item = inventory_item
        if resolved_inventory_item is None:
            resolved_inventory_item = cls.ensure_inventory_item(
                inventory=inventory,
                stock_item=stock_item,
                purchase_order_line=purchase_order_line,
                actor_user_id=actor_user_id,
            )

        legacy_inventory = inventory or cls.resolve_legacy_inventory(resolved_inventory_item)
        if legacy_inventory is None and stock_item is not None and stock_item.inventory_id:
            legacy_inventory = stock_item.inventory
        if legacy_inventory is None and purchase_order_line and purchase_order_line.stock_item_id:
            legacy_inventory = purchase_order_line.stock_item.inventory

        profile_source = resolved_inventory_item if resolved_inventory_item is not None else legacy_inventory
        profile_id = cls._resolve_profile_id(profile_source)
        return resolved_inventory_item, legacy_inventory, profile_id

    @classmethod
    def ensure_inventory_item(
        cls,
        *,
        inventory: Inventory | None = None,
        stock_item: StockItem | None = None,
        purchase_order_line: PurchaseOrderLineItem | None = None,
        actor_user_id=None,
    ) -> InventoryItem:
        if purchase_order_line and purchase_order_line.inventory_item_id:
            return purchase_order_line.inventory_item

        if inventory is None:
            if stock_item and stock_item.inventory_id:
                inventory = stock_item.inventory
            elif purchase_order_line and purchase_order_line.stock_item_id and purchase_order_line.stock_item.inventory_id:
                inventory = purchase_order_line.stock_item.inventory

        if inventory is None:
            raise StockDomainError("Unable to resolve inventory item for stock operation.")

        profile_id = cls._resolve_profile_id(inventory)
        inventory_item = (
            InventoryItem.objects.filter(
                metadata__legacy_inventory_id=str(inventory.id)
            )
            .order_by("created_at")
            .first()
        )
        changed = inventory_item is None
        if inventory_item is None:
            inventory_item = InventoryItem(
                profile_id=profile_id,
                name_snapshot=inventory.name,
                sku_snapshot="",
                barcode_snapshot="",
                description=inventory.description or "",
                inventory_category=inventory.category,
                inventory_type=inventory.inventory_type,
                default_uom_code=inventory.unit or "",
                stock_uom_code=inventory.unit_name or "",
                track_stock=True,
                track_lot=inventory.batch_tracking_enabled,
                track_serial=inventory.trackable,
                track_expiry=bool(inventory.expiration_threshold),
                allow_negative_stock=False,
                reorder_point=inventory.re_order_point,
                reorder_quantity=inventory.re_order_quantity,
                minimum_stock_level=inventory.minimum_stock_level,
                safety_stock_level=inventory.safety_stock_level,
                default_supplier=inventory.default_supplier,
                metadata={"legacy_inventory_id": str(inventory.id)},
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )

        metadata = dict(inventory_item.metadata or {})
        if metadata.get("legacy_inventory_id") != str(inventory.id):
            metadata["legacy_inventory_id"] = str(inventory.id)
            inventory_item.metadata = metadata
            changed = True
        field_updates = {
            "profile_id": profile_id,
            "name_snapshot": inventory.name,
            "description": inventory.description or "",
            "inventory_category": inventory.category,
            "inventory_type": inventory.inventory_type,
            "default_uom_code": inventory.unit or "",
            "stock_uom_code": inventory.unit_name or "",
            "track_stock": True,
            "track_lot": inventory.batch_tracking_enabled,
            "track_serial": inventory.trackable,
            "track_expiry": bool(inventory.expiration_threshold),
            "allow_negative_stock": False,
            "default_supplier": inventory.default_supplier,
            "reorder_point": inventory.re_order_point,
            "reorder_quantity": inventory.re_order_quantity,
            "minimum_stock_level": inventory.minimum_stock_level,
            "safety_stock_level": inventory.safety_stock_level,
        }
        for field_name, field_value in field_updates.items():
            if getattr(inventory_item, field_name) != field_value:
                setattr(inventory_item, field_name, field_value)
                changed = True

        catalog_variant = cls._resolve_catalog_variant_projection(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_item=stock_item,
            purchase_order_line=purchase_order_line,
        )
        if catalog_variant is not None:
            metadata = dict(inventory_item.metadata or {})
            variant_barcode = catalog_variant.variant_barcode or metadata.get("legacy_variant_barcode", "")
            catalog_updates = {
                "product_template_id": catalog_variant.product_id,
                "product_variant_id": catalog_variant.variant_id,
                "barcode_snapshot": variant_barcode or inventory_item.barcode_snapshot,
                "sku_snapshot": catalog_variant.variant_sku or inventory_item.sku_snapshot,
            }
            for field_name, field_value in catalog_updates.items():
                if field_value and getattr(inventory_item, field_name) != field_value:
                    setattr(inventory_item, field_name, field_value)
                    changed = True
            if variant_barcode and metadata.get("legacy_variant_barcode") != variant_barcode:
                metadata["legacy_variant_barcode"] = variant_barcode
                inventory_item.metadata = metadata
                changed = True

        if changed:
            inventory_item.updated_by_user_id = actor_user_id
            inventory_item.save()

        if purchase_order_line and purchase_order_line.inventory_item_id != inventory_item.id:
            purchase_order_line.inventory_item = inventory_item
            purchase_order_line.updated_by_user_id = actor_user_id
            purchase_order_line.save()
        return inventory_item

    @classmethod
    def _resolve_stock_serial(
        cls,
        *,
        profile_id: int,
        inventory_item: InventoryItem,
        stock_location: StockLocation | None = None,
        stock_lot: StockLot | None = None,
        stock_serial: StockSerial | None = None,
        serial_number: str = "",
        allowed_statuses: list[str] | None = None,
    ) -> StockSerial:
        queryset = StockSerial.objects.select_for_update().filter(
            profile_id=profile_id,
            inventory_item=inventory_item,
        )
        if stock_serial is not None:
            queryset = queryset.filter(id=stock_serial.id)
        elif serial_number:
            queryset = queryset.filter(serial_number=str(serial_number).strip())
        else:
            raise StockDomainError(
                "Serial-tracked inventory requires a stock_serial or serial_number for this operation."
            )

        if stock_location is not None:
            queryset = queryset.filter(stock_location=stock_location)
        if stock_lot is not None:
            queryset = queryset.filter(stock_lot=stock_lot)
        if allowed_statuses:
            queryset = queryset.filter(status__in=allowed_statuses)

        resolved_serial = queryset.first()
        if resolved_serial is None:
            raise StockDomainError("The requested stock serial could not be found for this operation.")
        return resolved_serial

    @classmethod
    @transaction.atomic
    def reserve_stock(
        cls,
        *,
        inventory: Inventory | None = None,
        inventory_item: InventoryItem | None = None,
        stock_location: StockLocation,
        quantity,
        external_order_type: str,
        external_order_id: str,
        external_order_line_id: str = "",
        actor_user_id=None,
        stock_lot: StockLot | None = None,
        stock_serial: StockSerial | None = None,
        serial_number: str = "",
        expires_at=None,
        notes: str = "",
    ):
        quantity = _to_decimal(quantity)
        if quantity <= 0:
            raise StockDomainError("Reservation quantity must be greater than zero.")

        inventory_item, legacy_inventory, profile_id = cls._resolve_inventory_context(
            inventory=inventory,
            inventory_item=inventory_item,
            actor_user_id=actor_user_id,
        )
        if inventory_item.track_serial:
            reservation_count = _to_whole_number(quantity, label="Reservation quantity")
            if reservation_count != 1:
                raise StockDomainError("Serial-tracked inventory can only reserve one serial per reservation.")
            stock_serial = cls._resolve_stock_serial(
                profile_id=profile_id,
                inventory_item=inventory_item,
                stock_location=stock_location,
                stock_lot=stock_lot,
                stock_serial=stock_serial,
                serial_number=serial_number,
                allowed_statuses=[StockSerialStatus.AVAILABLE],
            )
            if stock_lot is None and stock_serial.stock_lot_id:
                stock_lot = stock_serial.stock_lot
        elif stock_serial is not None or serial_number:
            raise StockDomainError("Serial selection is only valid for serial-tracked inventory.")

        if stock_lot is None and inventory_item.track_lot:
            candidate_balance = (
                StockBalance.objects.select_for_update()
                .filter(
                    profile_id=profile_id,
                    inventory_item=inventory_item,
                    stock_location=stock_location,
                    stock_lot__isnull=False,
                    quantity_available__gte=quantity,
                )
                .select_related('stock_lot')
                .order_by('stock_lot__expiry_date', 'created_at')
                .first()
            )
            if candidate_balance is None:
                raise StockDomainError(
                    "Lot-tracked inventory requires a stock lot with enough available quantity for reservation."
                )
            stock_lot = candidate_balance.stock_lot

        if stock_lot and stock_lot.inventory_item_id != inventory_item.id:
            raise StockDomainError("Stock lot does not belong to the selected inventory item.")

        balance = cls._get_locked_balance(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_location,
            stock_lot=stock_lot,
            legacy_inventory=legacy_inventory,
            actor_user_id=actor_user_id,
        )
        if _to_decimal(balance.quantity_available) < quantity and not inventory_item.allow_negative_stock:
            raise StockDomainError("Insufficient available stock to reserve.")

        balance.quantity_reserved = _to_decimal(balance.quantity_reserved) + quantity
        balance.updated_by_user_id = actor_user_id
        balance.save()

        reservation = StockReservation.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_lot=stock_lot,
            stock_serial=stock_serial,
            stock_location=stock_location,
            external_order_type=external_order_type,
            external_order_id=external_order_id,
            external_order_line_id=external_order_line_id or "",
            reserved_quantity=quantity,
            fulfilled_quantity=Decimal("0"),
            status=StockReservationStatus.ACTIVE,
            expires_at=expires_at,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        if stock_serial is not None:
            stock_serial.status = StockSerialStatus.RESERVED
            stock_serial.updated_by_user_id = actor_user_id
            stock_serial.save()

        StockMovement.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_lot=stock_lot,
            stock_serial=stock_serial,
            from_location=stock_location,
            movement_type=StockMovementType.RESERVATION,
            quantity=quantity,
            reference_type=external_order_type,
            reference_id=external_order_line_id or external_order_id,
            actor_user_id=actor_user_id,
            notes=notes or f"Reserved for {external_order_type}:{external_order_id}",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        cls._publish_inventory_availability_on_commit(inventory_item.id)
        cls._publish_inventory_reservation_on_commit(reservation.id)
        return {
            "reservation": reservation,
            "balance": balance,
        }

    @classmethod
    @transaction.atomic
    def issue_stock(
        cls,
        *,
        inventory: Inventory | None = None,
        inventory_item: InventoryItem | None = None,
        purchase_order_line: PurchaseOrderLineItem | None = None,
        stock_location: StockLocation,
        quantity,
        actor_user_id=None,
        stock_lot: StockLot | None = None,
        stock_serial: StockSerial | None = None,
        serial_number: str = "",
        reference_type: str = "",
        reference_id: str = "",
        notes: str = "",
        movement_type: str = StockMovementType.ISSUE,
        tracking_type: int = TrackingType.SHIPPED,
    ):
        quantity = _to_decimal(quantity)
        if quantity <= 0:
            raise StockDomainError("Issue quantity must be greater than zero.")

        inventory_item, legacy_inventory, profile_id = cls._resolve_inventory_context(
            inventory=inventory,
            inventory_item=inventory_item,
            purchase_order_line=purchase_order_line,
            actor_user_id=actor_user_id,
        )
        if inventory_item.track_serial:
            issue_count = _to_whole_number(quantity, label="Issue quantity")
            if issue_count != 1:
                raise StockDomainError("Serial-tracked inventory can only issue one serial per operation.")
            stock_serial = cls._resolve_stock_serial(
                profile_id=profile_id,
                inventory_item=inventory_item,
                stock_location=stock_location,
                stock_lot=stock_lot,
                stock_serial=stock_serial,
                serial_number=serial_number,
                allowed_statuses=[StockSerialStatus.AVAILABLE],
            )
            if stock_lot is None and stock_serial.stock_lot_id:
                stock_lot = stock_serial.stock_lot
        elif stock_serial is not None or serial_number:
            raise StockDomainError("Serial selection is only valid for serial-tracked inventory.")

        if stock_lot is None and inventory_item.track_lot:
            candidate_balance = (
                StockBalance.objects.select_for_update()
                .filter(
                    profile_id=profile_id,
                    inventory_item=inventory_item,
                    stock_location=stock_location,
                    stock_lot__isnull=False,
                    quantity_available__gte=quantity,
                )
                .select_related('stock_lot')
                .order_by('stock_lot__expiry_date', 'created_at')
                .first()
            )
            if candidate_balance is None:
                raise StockDomainError(
                    "Lot-tracked inventory requires a stock lot with enough available quantity to issue."
                )
            stock_lot = candidate_balance.stock_lot

        if stock_lot and stock_lot.inventory_item_id != inventory_item.id:
            raise StockDomainError("Stock lot does not belong to the selected inventory item.")

        balance = cls._get_locked_balance(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_location,
            stock_lot=stock_lot,
            legacy_inventory=legacy_inventory,
            actor_user_id=actor_user_id,
        )

        quantity_on_hand = _to_decimal(balance.quantity_on_hand)
        quantity_available = _to_decimal(balance.quantity_available)
        if quantity_available < quantity and not inventory_item.allow_negative_stock:
            raise StockDomainError("Insufficient available stock to issue.")
        if quantity_on_hand < quantity and not inventory_item.allow_negative_stock:
            raise StockDomainError("Insufficient stock on hand to issue.")

        balance.quantity_on_hand = quantity_on_hand - quantity
        balance.updated_by_user_id = actor_user_id
        balance.save()

        if stock_lot is not None:
            stock_lot.remaining_quantity = max(
                _to_decimal(stock_lot.remaining_quantity) - quantity,
                Decimal("0"),
            )
            stock_lot.updated_by_user_id = actor_user_id
            stock_lot.save()

        if stock_serial is not None:
            stock_serial.status = StockSerialStatus.ISSUED
            stock_serial.stock_location = None
            stock_serial.updated_by_user_id = actor_user_id
            stock_serial.save()

        StockMovement.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_lot=stock_lot,
            stock_serial=stock_serial,
            from_location=stock_location,
            movement_type=movement_type,
            quantity=quantity,
            unit_cost=cls._resolve_inventory_unit_cost(
                inventory_item=inventory_item,
                stock_lot=stock_lot,
                stock_location=stock_location,
            ),
            reference_type=reference_type,
            reference_id=reference_id,
            actor_user_id=actor_user_id,
            notes=notes or f"Issued stock for {reference_type}:{reference_id}",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        cls._publish_inventory_availability_on_commit(inventory_item.id)
        return {
            "inventory_item": inventory_item,
            "balance": balance,
            "stock_lot": stock_lot,
        }

    @classmethod
    @transaction.atomic
    def release_reservation(
        cls,
        *,
        reservation: StockReservation,
        quantity=None,
        actor_user_id=None,
        notes: str = "",
    ):
        release_quantity = _to_decimal(quantity or reservation.remaining_quantity)
        if release_quantity <= 0:
            raise StockDomainError("Release quantity must be greater than zero.")
        if release_quantity > reservation.remaining_quantity:
            raise StockDomainError("Cannot release more than the remaining reserved quantity.")
        if reservation.stock_serial_id:
            release_count = _to_whole_number(release_quantity, label="Release quantity")
            if release_count != 1:
                raise StockDomainError("Serial-tracked reservations can only release one serial at a time.")

        balance = cls._get_locked_balance(
            profile_id=reservation.profile_id,
            inventory_item=reservation.inventory_item,
            stock_location=reservation.stock_location,
            stock_lot=reservation.stock_lot,
            legacy_inventory=cls.resolve_legacy_inventory(reservation.inventory_item),
            actor_user_id=actor_user_id,
        )
        balance.quantity_reserved = max(
            _to_decimal(balance.quantity_reserved) - release_quantity,
            Decimal("0"),
        )
        balance.updated_by_user_id = actor_user_id
        balance.save()

        if release_quantity == reservation.remaining_quantity and reservation.fulfilled_quantity <= 0:
            reservation.status = StockReservationStatus.RELEASED
        else:
            reservation.status = StockReservationStatus.PARTIALLY_FULFILLED
        reservation.updated_by_user_id = actor_user_id
        reservation.save()

        if reservation.stock_serial_id and release_quantity > 0:
            reservation.stock_serial.status = StockSerialStatus.AVAILABLE
            reservation.stock_serial.stock_location = reservation.stock_location
            reservation.stock_serial.updated_by_user_id = actor_user_id
            reservation.stock_serial.save()

        StockMovement.objects.create(
            profile_id=reservation.profile_id,
            inventory_item=reservation.inventory_item,
            stock_lot=reservation.stock_lot,
            stock_serial=reservation.stock_serial,
            to_location=reservation.stock_location,
            movement_type=StockMovementType.RELEASE,
            quantity=release_quantity,
            reference_type=reservation.external_order_type,
            reference_id=reservation.external_order_line_id or reservation.external_order_id,
            actor_user_id=actor_user_id,
            notes=notes or f"Released reservation {reservation.id}",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        cls._publish_inventory_availability_on_commit(reservation.inventory_item_id)
        cls._publish_inventory_reservation_release_on_commit(reservation.id)
        return {
            "reservation": reservation,
            "balance": balance,
        }

    @classmethod
    @transaction.atomic
    def fulfill_reservation(
        cls,
        *,
        reservation: StockReservation,
        quantity=None,
        actor_user_id=None,
        notes: str = "",
    ):
        fulfill_quantity = _to_decimal(quantity or reservation.remaining_quantity)
        if fulfill_quantity <= 0:
            raise StockDomainError("Fulfillment quantity must be greater than zero.")
        if fulfill_quantity > reservation.remaining_quantity:
            raise StockDomainError("Cannot fulfill more than the remaining reserved quantity.")
        if reservation.stock_serial_id:
            fulfill_count = _to_whole_number(fulfill_quantity, label="Fulfillment quantity")
            if fulfill_count != 1:
                raise StockDomainError("Serial-tracked reservations can only fulfill one serial at a time.")

        inventory_item = reservation.inventory_item
        balance = cls._get_locked_balance(
            profile_id=reservation.profile_id,
            inventory_item=inventory_item,
            stock_location=reservation.stock_location,
            stock_lot=reservation.stock_lot,
            legacy_inventory=cls.resolve_legacy_inventory(inventory_item),
            actor_user_id=actor_user_id,
        )
        if _to_decimal(balance.quantity_reserved) < fulfill_quantity:
            raise StockDomainError("Balance reserved quantity is lower than the requested fulfillment quantity.")
        if (
            _to_decimal(balance.quantity_on_hand) < fulfill_quantity
            and not inventory_item.allow_negative_stock
        ):
            raise StockDomainError("Insufficient stock on hand to fulfill reservation.")

        balance.quantity_reserved = _to_decimal(balance.quantity_reserved) - fulfill_quantity
        balance.quantity_on_hand = _to_decimal(balance.quantity_on_hand) - fulfill_quantity
        balance.updated_by_user_id = actor_user_id
        balance.save()

        if reservation.stock_lot_id:
            reservation.stock_lot.remaining_quantity = max(
                _to_decimal(reservation.stock_lot.remaining_quantity) - fulfill_quantity,
                Decimal("0"),
            )
            reservation.stock_lot.updated_by_user_id = actor_user_id
            reservation.stock_lot.save()

        reservation.fulfilled_quantity = _to_decimal(reservation.fulfilled_quantity) + fulfill_quantity
        reservation.status = (
            StockReservationStatus.FULFILLED
            if reservation.fulfilled_quantity >= reservation.reserved_quantity
            else StockReservationStatus.PARTIALLY_FULFILLED
        )
        reservation.updated_by_user_id = actor_user_id
        reservation.save()

        if reservation.stock_serial_id:
            reservation.stock_serial.status = StockSerialStatus.ISSUED
            reservation.stock_serial.stock_location = None
            reservation.stock_serial.updated_by_user_id = actor_user_id
            reservation.stock_serial.save()

        StockMovement.objects.create(
            profile_id=reservation.profile_id,
            inventory_item=inventory_item,
            stock_lot=reservation.stock_lot,
            stock_serial=reservation.stock_serial,
            from_location=reservation.stock_location,
            movement_type=StockMovementType.ISSUE,
            quantity=fulfill_quantity,
            unit_cost=cls._resolve_inventory_unit_cost(
                inventory_item=inventory_item,
                stock_lot=reservation.stock_lot,
                stock_location=reservation.stock_location,
            ),
            reference_type=reservation.external_order_type,
            reference_id=reservation.external_order_line_id or reservation.external_order_id,
            actor_user_id=actor_user_id,
            notes=notes or f"Fulfilled reservation {reservation.id}",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        cls._publish_inventory_availability_on_commit(inventory_item.id)
        cls._publish_inventory_fulfillment_on_commit(reservation.id)
        return {
            "reservation": reservation,
            "balance": balance,
        }

    @classmethod
    def resolve_legacy_inventory(cls, inventory_item: InventoryItem | None):
        if inventory_item is None:
            return None
        legacy_inventory_id = (inventory_item.metadata or {}).get("legacy_inventory_id")
        if not legacy_inventory_id:
            return None
        return Inventory.objects.filter(id=legacy_inventory_id).first()

    @classmethod
    def resolve_stock_lot(
        cls,
        *,
        stock_item: StockItem,
        inventory_item: InventoryItem,
    ):
        profile_id = cls._resolve_profile_id(stock_item.inventory)
        if not stock_item.batch:
            return None
        return StockLot.objects.filter(
            profile_id=profile_id,
            inventory_item=inventory_item,
            lot_number=stock_item.batch,
        ).order_by("-created_at").first()

    @classmethod
    def _resolve_inventory_unit_cost(
        cls,
        *,
        inventory_item: InventoryItem,
        stock_lot: StockLot | None = None,
        stock_location: StockLocation | None = None,
    ):
        if stock_lot is not None and stock_lot.unit_cost is not None:
            return stock_lot.unit_cost

        movement_queryset = StockMovement.objects.filter(
            inventory_item=inventory_item,
            unit_cost__isnull=False,
        )
        if stock_location is not None:
            movement_queryset = movement_queryset.filter(
                models.Q(to_location=stock_location) | models.Q(from_location=stock_location)
            )
        latest_movement = movement_queryset.order_by("-occurred_at", "-created_at").first()
        if latest_movement is not None:
            return latest_movement.unit_cost

        latest_lot = inventory_item.stock_lots.exclude(unit_cost__isnull=True).order_by("-created_at").first()
        if latest_lot is not None:
            return latest_lot.unit_cost
        return None

    @classmethod
    def _resolve_profile_id(cls, source) -> int:
        profile_id = _coerce_profile_id(getattr(source, "profile_id", None))
        if profile_id is None:
            profile_id = _coerce_profile_id(getattr(source, "profile", None))
        if profile_id is None:
            raise StockDomainError("Tenant profile_id is required for stock operations.")
        return profile_id

    @classmethod
    def _resolve_catalog_variant_projection(
        cls,
        *,
        profile_id: int,
        inventory_item: InventoryItem | None = None,
        stock_item: StockItem | None = None,
        purchase_order_line: PurchaseOrderLineItem | None = None,
    ):
        from mainapps.projections.models import CatalogVariantProjection

        queryset = CatalogVariantProjection.objects.select_related("product").filter(profile_id=profile_id)
        if inventory_item and inventory_item.product_variant_id:
            variant = queryset.filter(variant_id=inventory_item.product_variant_id).first()
            if variant is not None:
                return variant

        candidate_values: list[str] = []
        metadata = inventory_item.metadata if inventory_item and isinstance(inventory_item.metadata, dict) else {}
        for raw_value in [
            inventory_item.barcode_snapshot if inventory_item is not None else "",
            metadata.get("legacy_variant_barcode", ""),
            inventory_item.sku_snapshot if inventory_item is not None else "",
            stock_item.sku if stock_item is not None else "",
            (
                purchase_order_line.stock_item.sku
                if purchase_order_line is not None and purchase_order_line.stock_item_id
                else ""
            ),
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

    @classmethod
    def _publish_inventory_availability_on_commit(cls, inventory_item_id) -> None:
        from subapps.kafka.producers.inventory import publish_inventory_availability_upserted

        transaction.on_commit(
            lambda item_id=inventory_item_id: publish_inventory_availability_upserted(inventory_item_id=item_id)
        )

    @classmethod
    def _publish_inventory_reservation_on_commit(cls, reservation_id) -> None:
        from subapps.kafka.producers.inventory import publish_inventory_reservation_upserted

        transaction.on_commit(
            lambda record_id=reservation_id: publish_inventory_reservation_upserted(reservation_id=record_id)
        )

    @classmethod
    def _publish_inventory_reservation_release_on_commit(cls, reservation_id) -> None:
        from subapps.kafka.producers.inventory import publish_inventory_reservation_released

        transaction.on_commit(
            lambda record_id=reservation_id: publish_inventory_reservation_released(reservation_id=record_id)
        )

    @classmethod
    def _publish_inventory_fulfillment_on_commit(cls, reservation_id) -> None:
        from subapps.kafka.producers.inventory import publish_inventory_fulfillment_completed

        transaction.on_commit(
            lambda record_id=reservation_id: publish_inventory_fulfillment_completed(reservation_id=record_id)
        )

    @classmethod
    def _get_locked_balance(
        cls,
        *,
        profile_id: int,
        inventory_item: InventoryItem,
        stock_location: StockLocation,
        stock_lot: StockLot | None = None,
        legacy_inventory: Inventory | None = None,
        actor_user_id=None,
    ) -> StockBalance:
        balance = StockBalance.objects.select_for_update().filter(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_location,
            stock_lot=stock_lot,
        ).first()
        if balance is not None:
            return balance

        return StockBalance.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_location,
            stock_lot=stock_lot,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

    @classmethod
    def _create_receipt_serials(
        cls,
        *,
        profile_id: int,
        inventory_item: InventoryItem,
        stock_location: StockLocation,
        stock_lot: StockLot | None = None,
        serial_numbers: list[str] | None = None,
        actor_user_id=None,
    ) -> list[StockSerial]:
        serial_numbers = _normalize_serial_numbers(serial_numbers)
        if not serial_numbers:
            return []

        stock_serials: list[StockSerial] = []
        for serial_number in serial_numbers:
            if StockSerial.objects.filter(profile_id=profile_id, serial_number=serial_number).exists():
                raise StockDomainError(f"Serial number '{serial_number}' already exists.")
            stock_serials.append(
                StockSerial.objects.create(
                    profile_id=profile_id,
                    inventory_item=inventory_item,
                    stock_lot=stock_lot,
                    stock_location=stock_location,
                    serial_number=serial_number,
                    status=StockSerialStatus.AVAILABLE,
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
            )
        return stock_serials
