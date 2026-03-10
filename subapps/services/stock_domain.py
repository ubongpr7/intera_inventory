from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from mainapps.inventory.models import Inventory, InventoryItem
from mainapps.orders.models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLineItem
from mainapps.stock.models import (
    StockBalance,
    StockItem,
    StockItemTracking,
    StockLocation,
    StockLot,
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
        inventory = cls.resolve_legacy_inventory(inventory_item) or (
            line_item.stock_item.inventory if line_item.stock_item_id else None
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
            legacy_inventory=inventory,
            actor_user_id=actor_user_id,
        )
        balance.quantity_on_hand = _to_decimal(balance.quantity_on_hand) + quantity_received
        balance.updated_by_user_id = actor_user_id
        balance.save()

        legacy_stock_item = cls._ensure_receipt_legacy_stock_item(
            purchase_order=purchase_order,
            purchase_order_line=line_item,
            inventory=inventory,
            inventory_item=inventory_item,
            stock_location=stock_location,
            unit_cost=line_item.unit_price,
            quantity_received=quantity_received,
            lot_number=stock_lot.lot_number if stock_lot else line_lot_number,
            expiry_date=line_expiry_date,
            actor_user_id=actor_user_id,
            notes=notes,
        )

        line_item.quantity_received = _to_decimal(line_item.quantity_received) + quantity_received
        line_item.updated_by_user_id = actor_user_id
        if legacy_stock_item and not line_item.stock_item_id:
            line_item.stock_item = legacy_stock_item
        line_item.save()

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

        return {
            "goods_receipt_line": goods_receipt_line,
            "stock_lot": stock_lot,
            "balance": balance,
            "legacy_stock_item": legacy_stock_item,
        }

    @classmethod
    @transaction.atomic
    def transfer_stock(
        cls,
        *,
        stock_item: StockItem,
        to_location: StockLocation,
        quantity,
        actor_user_id=None,
        notes: str = "",
    ):
        quantity = _to_decimal(quantity)
        if quantity <= 0:
            raise StockDomainError("Transfer quantity must be greater than zero.")
        if stock_item.location_id is None:
            raise StockDomainError("Source stock item must have a location.")
        if stock_item.location_id == to_location.id:
            raise StockDomainError("Source and destination locations must be different.")

        inventory_item = cls.ensure_inventory_item(stock_item=stock_item, actor_user_id=actor_user_id)
        profile_id = cls._resolve_profile_id(stock_item.inventory)
        stock_lot = cls.resolve_stock_lot(stock_item=stock_item, inventory_item=inventory_item)

        source_balance = cls._get_locked_balance(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_item.location,
            stock_lot=stock_lot,
            legacy_inventory=stock_item.inventory,
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
            legacy_inventory=stock_item.inventory,
            actor_user_id=actor_user_id,
        )
        destination_balance.quantity_on_hand = _to_decimal(destination_balance.quantity_on_hand) + quantity
        destination_balance.updated_by_user_id = actor_user_id
        destination_balance.save()

        destination_stock_item = cls._ensure_transfer_destination_stock_item(
            stock_item=stock_item,
            to_location=to_location,
            quantity=quantity,
            actor_user_id=actor_user_id,
        )

        stock_item.quantity = _to_decimal(stock_item.quantity) - quantity
        stock_item.updated_by_user_id = actor_user_id
        stock_item.save()

        StockItemTracking.objects.create(
            inventory=stock_item.inventory,
            item=stock_item,
            tracking_type=TrackingType.LOCATION_CHANGE,
            notes=notes or f"Transferred {quantity} units to {to_location.name}",
            performed_by_user_id=actor_user_id,
            deltas={
                "quantity_after": float(stock_item.quantity),
                "quantity_change": float(-quantity),
                "to_location_id": str(to_location.id),
            },
        )

        StockMovement.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_lot=stock_lot,
            from_location=stock_item.location,
            to_location=to_location,
            movement_type=StockMovementType.TRANSFER,
            quantity=quantity,
            unit_cost=stock_item.purchase_price,
            reference_type="stock_item",
            reference_id=str(stock_item.id),
            actor_user_id=actor_user_id,
            notes=notes or f"Transferred from {stock_item.location.name} to {to_location.name}",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        return {
            "source_balance": source_balance,
            "destination_balance": destination_balance,
            "destination_stock_item": destination_stock_item,
        }

    @classmethod
    @transaction.atomic
    def adjust_stock(
        cls,
        *,
        inventory: Inventory,
        stock_location: StockLocation,
        quantity_change,
        actor_user_id=None,
        reason: str = "",
    ):
        quantity_change = _to_decimal(quantity_change)
        if quantity_change == 0:
            raise StockDomainError("Quantity change cannot be zero.")

        inventory_item = cls.ensure_inventory_item(inventory=inventory, actor_user_id=actor_user_id)
        profile_id = cls._resolve_profile_id(inventory)

        balance = cls._get_locked_balance(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_location,
            legacy_inventory=inventory,
            actor_user_id=actor_user_id,
        )
        previous_quantity = _to_decimal(balance.quantity_on_hand)
        next_quantity = previous_quantity + quantity_change
        if next_quantity < 0 and not inventory_item.allow_negative_stock:
            raise StockDomainError("Insufficient stock quantity.")

        balance.quantity_on_hand = next_quantity
        balance.updated_by_user_id = actor_user_id
        balance.save()

        legacy_stock_item = cls._ensure_adjustment_legacy_stock_item(
            inventory=inventory,
            inventory_item=inventory_item,
            stock_location=stock_location,
            quantity_change=quantity_change,
            actor_user_id=actor_user_id,
        )

        if legacy_stock_item is not None:
            StockItemTracking.objects.create(
                inventory=inventory,
                item=legacy_stock_item,
                tracking_type=TrackingType.STOCK_ADJUSTMENT,
                notes=f"Manual adjustment: {reason}",
                performed_by_user_id=actor_user_id,
                deltas={
                    "quantity_before": float(previous_quantity),
                    "quantity_after": float(next_quantity),
                    "quantity_change": float(quantity_change),
                },
            )

        StockMovement.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            from_location=stock_location if quantity_change < 0 else None,
            to_location=stock_location if quantity_change > 0 else None,
            movement_type=StockMovementType.ADJUSTMENT,
            quantity=quantity_change,
            reference_type="inventory",
            reference_id=str(inventory.id),
            actor_user_id=actor_user_id,
            notes=reason or "Manual stock adjustment",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        return {
            "balance": balance,
            "legacy_stock_item": legacy_stock_item,
            "old_quantity": previous_quantity,
            "new_quantity": next_quantity,
        }

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
        if stock_item and stock_item.inventory_item_id:
            return stock_item.inventory_item

        if inventory is None:
            if stock_item and stock_item.inventory_id:
                inventory = stock_item.inventory
            elif purchase_order_line and purchase_order_line.stock_item_id and purchase_order_line.stock_item.inventory_id:
                inventory = purchase_order_line.stock_item.inventory

        if inventory is None:
            raise StockDomainError("Unable to resolve inventory item for stock operation.")

        profile_id = cls._resolve_profile_id(inventory)
        bridge_id = InventoryItem.legacy_bridge_id(inventory.id)
        inventory_item, created = InventoryItem.objects.get_or_create(
            id=bridge_id,
            defaults={
                "profile_id": profile_id,
                "name_snapshot": inventory.name,
                "sku_snapshot": inventory.external_system_id or "",
                "barcode_snapshot": "",
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
                "reorder_point": inventory.re_order_point,
                "reorder_quantity": inventory.re_order_quantity,
                "minimum_stock_level": inventory.minimum_stock_level,
                "safety_stock_level": inventory.safety_stock_level,
                "default_supplier": inventory.default_supplier,
                "metadata": {"legacy_inventory_id": str(inventory.id)},
                "created_by_user_id": actor_user_id,
                "updated_by_user_id": actor_user_id,
            },
        )

        changed = False
        metadata = dict(inventory_item.metadata or {})
        if metadata.get("legacy_inventory_id") != str(inventory.id):
            metadata["legacy_inventory_id"] = str(inventory.id)
            inventory_item.metadata = metadata
            changed = True
        field_updates = {
            "profile_id": profile_id,
            "name_snapshot": inventory.name,
            "sku_snapshot": inventory.external_system_id or "",
            "description": inventory.description or "",
            "inventory_category": inventory.category,
            "inventory_type": inventory.inventory_type,
            "default_uom_code": inventory.unit or "",
            "stock_uom_code": inventory.unit_name or "",
            "track_lot": inventory.batch_tracking_enabled,
            "track_serial": inventory.trackable,
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

        if changed and not created:
            inventory_item.updated_by_user_id = actor_user_id
            inventory_item.save()

        if stock_item and stock_item.inventory_item_id != inventory_item.id:
            stock_item.inventory_item = inventory_item
            stock_item.updated_by_user_id = actor_user_id
            stock_item.save()
        if purchase_order_line and purchase_order_line.inventory_item_id != inventory_item.id:
            purchase_order_line.inventory_item = inventory_item
            purchase_order_line.updated_by_user_id = actor_user_id
            purchase_order_line.save()
        return inventory_item

    @classmethod
    @transaction.atomic
    def reserve_stock(
        cls,
        *,
        inventory: Inventory,
        stock_location: StockLocation,
        quantity,
        external_order_type: str,
        external_order_id: str,
        external_order_line_id: str = "",
        actor_user_id=None,
        stock_lot: StockLot | None = None,
        expires_at=None,
        notes: str = "",
    ):
        quantity = _to_decimal(quantity)
        if quantity <= 0:
            raise StockDomainError("Reservation quantity must be greater than zero.")

        inventory_item = cls.ensure_inventory_item(inventory=inventory, actor_user_id=actor_user_id)
        profile_id = cls._resolve_profile_id(inventory)
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
            legacy_inventory=inventory,
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

        StockMovement.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_lot=stock_lot,
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

        return {
            "reservation": reservation,
            "balance": balance,
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

        StockMovement.objects.create(
            profile_id=reservation.profile_id,
            inventory_item=reservation.inventory_item,
            stock_lot=reservation.stock_lot,
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

        legacy_inventory = cls.resolve_legacy_inventory(inventory_item)
        legacy_stock_item = cls._find_legacy_stock_item(
            inventory=legacy_inventory,
            inventory_item=inventory_item,
            stock_location=reservation.stock_location,
            stock_lot=reservation.stock_lot,
        )
        if legacy_stock_item is not None:
            legacy_stock_item.quantity = _to_decimal(legacy_stock_item.quantity) - fulfill_quantity
            legacy_stock_item.updated_by_user_id = actor_user_id
            legacy_stock_item.save()
            StockItemTracking.objects.create(
                inventory=legacy_stock_item.inventory,
                item=legacy_stock_item,
                tracking_type=TrackingType.SHIPPED,
                notes=notes or f"Fulfilled reservation {reservation.id}",
                performed_by_user_id=actor_user_id,
                deltas={
                    "quantity_change": float(-fulfill_quantity),
                    "reservation_id": str(reservation.id),
                    "external_order_id": reservation.external_order_id,
                },
            )

        StockMovement.objects.create(
            profile_id=reservation.profile_id,
            inventory_item=inventory_item,
            stock_lot=reservation.stock_lot,
            from_location=reservation.stock_location,
            movement_type=StockMovementType.ISSUE,
            quantity=fulfill_quantity,
            unit_cost=reservation.stock_lot.unit_cost if reservation.stock_lot_id else None,
            reference_type=reservation.external_order_type,
            reference_id=reservation.external_order_line_id or reservation.external_order_id,
            actor_user_id=actor_user_id,
            notes=notes or f"Fulfilled reservation {reservation.id}",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

        return {
            "reservation": reservation,
            "balance": balance,
            "legacy_stock_item": legacy_stock_item,
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
    def _resolve_profile_id(cls, source) -> int:
        profile_id = _coerce_profile_id(getattr(source, "profile_id", None))
        if profile_id is None:
            profile_id = _coerce_profile_id(getattr(source, "profile", None))
        if profile_id is None:
            raise StockDomainError("Tenant profile_id is required for stock operations.")
        return profile_id

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

        quantity_on_hand = Decimal("0")
        if legacy_inventory is not None:
            quantity_on_hand = cls._legacy_location_quantity(
                inventory=legacy_inventory,
                stock_location=stock_location,
                inventory_item=inventory_item,
                stock_lot=stock_lot,
            )

        return StockBalance.objects.create(
            profile_id=profile_id,
            inventory_item=inventory_item,
            stock_location=stock_location,
            stock_lot=stock_lot,
            quantity_on_hand=quantity_on_hand,
            quantity_reserved=Decimal("0"),
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

    @classmethod
    def _legacy_location_quantity(
        cls,
        *,
        inventory: Inventory,
        stock_location: StockLocation,
        inventory_item: InventoryItem,
        stock_lot: StockLot | None = None,
    ) -> Decimal:
        queryset = StockItem.objects.filter(
            inventory=inventory,
            location=stock_location,
        )
        if stock_lot and stock_lot.lot_number:
            queryset = queryset.filter(batch=stock_lot.lot_number)
        aggregate = queryset.aggregate(total=Sum("quantity"))
        return _to_decimal(aggregate["total"] or 0)

    @classmethod
    def _ensure_receipt_legacy_stock_item(
        cls,
        *,
        purchase_order: PurchaseOrder,
        purchase_order_line: PurchaseOrderLineItem,
        inventory: Inventory | None,
        inventory_item: InventoryItem,
        stock_location: StockLocation,
        unit_cost,
        quantity_received,
        lot_number: str = "",
        expiry_date=None,
        actor_user_id=None,
        notes: str = "",
    ):
        if inventory is None:
            return None

        stock_item = purchase_order_line.stock_item
        if stock_item is None:
            queryset = StockItem.objects.filter(
                inventory=inventory,
                location=stock_location,
            )
            if inventory_item:
                queryset = queryset.filter(inventory_item=inventory_item)
            if lot_number:
                queryset = queryset.filter(batch=lot_number)
            stock_item = queryset.order_by("created_at").first()

        if stock_item is None:
            stock_item = StockItem(
                inventory=inventory,
                inventory_item=inventory_item,
                location=stock_location,
                purchase_order=purchase_order,
                name=inventory.name,
                quantity=Decimal("0"),
                purchase_price=unit_cost,
                batch=lot_number or purchase_order_line.batch_number or None,
                expiry_date=expiry_date,
                notes=notes or f"Received against PO {purchase_order.reference}",
                created_by_user_id=actor_user_id,
            )
        else:
            stock_item.inventory_item = inventory_item
            stock_item.location = stock_location
            if purchase_order and not stock_item.purchase_order_id:
                stock_item.purchase_order = purchase_order
            if lot_number and not stock_item.batch:
                stock_item.batch = lot_number
            if expiry_date and not stock_item.expiry_date:
                stock_item.expiry_date = expiry_date
            if unit_cost is not None:
                stock_item.purchase_price = unit_cost

        stock_item.quantity = _to_decimal(stock_item.quantity or 0) + _to_decimal(quantity_received)
        stock_item.updated_by_user_id = actor_user_id
        stock_item.save()

        StockItemTracking.objects.create(
            inventory=inventory,
            item=stock_item,
            tracking_type=TrackingType.RECEIVED,
            notes=notes or f"Received {quantity_received} units from PO {purchase_order.reference}",
            performed_by_user_id=actor_user_id,
            deltas={
                "quantity_received": float(quantity_received),
                "purchase_price": float(unit_cost),
                "purchase_order_id": str(purchase_order.id),
                "purchase_order_line_id": str(purchase_order_line.id),
            },
        )

        return stock_item

    @classmethod
    def _ensure_transfer_destination_stock_item(
        cls,
        *,
        stock_item: StockItem,
        to_location: StockLocation,
        quantity,
        actor_user_id=None,
    ):
        queryset = StockItem.objects.filter(
            inventory=stock_item.inventory,
            location=to_location,
        )
        if stock_item.inventory_item_id:
            queryset = queryset.filter(inventory_item=stock_item.inventory_item)
        if stock_item.batch:
            queryset = queryset.filter(batch=stock_item.batch)
        destination_stock_item = queryset.order_by("created_at").first()

        if destination_stock_item is None:
            destination_stock_item = StockItem(
                inventory=stock_item.inventory,
                inventory_item=stock_item.inventory_item,
                location=to_location,
                purchase_order=stock_item.purchase_order,
                name=stock_item.name,
                quantity=Decimal("0"),
                purchase_price=stock_item.purchase_price,
                product_variant=stock_item.product_variant,
                batch=stock_item.batch,
                expiry_date=stock_item.expiry_date,
                notes=stock_item.notes,
                created_by_user_id=actor_user_id,
            )

        destination_stock_item.quantity = _to_decimal(destination_stock_item.quantity or 0) + quantity
        destination_stock_item.updated_by_user_id = actor_user_id
        destination_stock_item.save()
        return destination_stock_item

    @classmethod
    def _ensure_adjustment_legacy_stock_item(
        cls,
        *,
        inventory: Inventory,
        inventory_item: InventoryItem,
        stock_location: StockLocation,
        quantity_change,
        actor_user_id=None,
    ):
        stock_item = StockItem.objects.filter(
            inventory=inventory,
            location=stock_location,
        ).order_by("created_at").first()

        if stock_item is None and quantity_change < 0:
            return None

        if stock_item is None:
            stock_item = StockItem(
                inventory=inventory,
                inventory_item=inventory_item,
                location=stock_location,
                name=inventory.name,
                quantity=Decimal("0"),
                created_by_user_id=actor_user_id,
            )

        stock_item.inventory_item = inventory_item
        stock_item.quantity = _to_decimal(stock_item.quantity or 0) + quantity_change
        stock_item.updated_by_user_id = actor_user_id
        stock_item.save()
        return stock_item

    @classmethod
    def _find_legacy_stock_item(
        cls,
        *,
        inventory: Inventory | None,
        inventory_item: InventoryItem,
        stock_location: StockLocation,
        stock_lot: StockLot | None = None,
    ):
        if inventory is None:
            return None
        queryset = StockItem.objects.filter(
            inventory=inventory,
            location=stock_location,
            inventory_item=inventory_item,
        )
        if stock_lot and stock_lot.lot_number:
            queryset = queryset.filter(batch=stock_lot.lot_number)
        return queryset.order_by("created_at").first()
