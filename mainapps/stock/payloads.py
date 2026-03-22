from decimal import Decimal
from enum import Enum
import uuid
from datetime import date, datetime
from typing import Optional, Dict, Any, List

from mainapps.inventory.payloads import InventoryItemResponsePayload

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ------------------------------------------------------------------------------
# Enumerations (mirror Django choices)
# ------------------------------------------------------------------------------

class StockStatus(str, Enum):
    OK = "ok"
    ATTENTION = "attention_needed"
    DAMAGED = "damaged"
    DESTROYED = "destroyed"
    REJECTED = "rejected"
    LOST = "lost"
    QUARANTINED = "quarantined"
    RETURNED = "returned"

class TrackingType(int, Enum):
    RECEIVED = 10
    PURCHASE_ORDER_RECEIPT = 11
    RETURNED_FROM_CUSTOMER = 12
    SHIPPED = 20
    SALES_ORDER_SHIPMENT = 21
    CONSUMED_IN_BUILD = 22
    STOCK_ADJUSTMENT = 30
    LOCATION_CHANGE = 31
    SPLIT_FROM_PARENT = 32
    MERGED_WITH_PARENT = 33
    QUARANTINED = 40
    QUALITY_CHECK = 41
    REJECTED = 42
    STOCKTAKE = 50
    AUTO_RESTOCK = 51
    EXPIRY_WARNING = 52
    STATUS_CHANGE = 60
    DAMAGE_REPORTED = 61
    OTHER = 0

class StockLotStatus(str, Enum):
    OPEN = "open"
    QUARANTINED = "quarantined"
    DEPLETED = "depleted"
    CLOSED = "closed"

class StockSerialStatus(str, Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    ISSUED = "issued"
    DAMAGED = "damaged"
    RETURNED = "returned"

class StockMovementType(str, Enum):
    RECEIPT = "receipt"
    ISSUE = "issue"
    TRANSFER = "transfer"
    ADJUSTMENT = "adjustment"
    RESERVATION = "reservation"
    RELEASE = "release"
    RETURN_IN = "return_in"
    RETURN_OUT = "return_out"

class StockReservationStatus(str, Enum):
    ACTIVE = "active"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"
    RELEASED = "released"
    EXPIRED = "expired"

class AdjustmentType(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    TRANSFER = "transfer"

# ------------------------------------------------------------------------------
# StockLocationType
# ------------------------------------------------------------------------------
class StockLocationTypeCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a StockLocationType."""
    name: str = Field(..., max_length=100, description="Brief name for the stock location type (unique)")
    description: Optional[str] = Field(None, max_length=250, description="Longer form description (optional)")

# ------------------------------------------------------------------------------
# StockLocation
# ------------------------------------------------------------------------------
class StockLocationCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a StockLocation."""
    code: Optional[str] = Field(None, max_length=100, description="Location code (auto-generated)")
    name: str = Field(..., max_length=200, description="Location name")
    official: Optional[str] = Field(None, max_length=255, description="Manager ID")
    official_user_id: Optional[int] = Field(None, description="User ID of manager")
    structural: bool = Field(False, description="If True, items cannot be placed here directly")
    parent_id: Optional[uuid.UUID] = Field(None, description="UUID of parent StockLocation")
    external: bool = Field(False, description="External location flag")
    location_type_id: Optional[uuid.UUID] = Field(None, description="UUID of StockLocationType")
    description: Optional[str] = Field(None, description="Longer form description")
    physical_address: Optional[str] = Field(None, max_length=255, description="Physical address")

# ------------------------------------------------------------------------------
# StockLot
# ------------------------------------------------------------------------------
class StockLotCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a StockLot."""
    inventory_item_id: uuid.UUID = Field(..., description="UUID of InventoryItem")
    supplier_id: Optional[uuid.UUID] = Field(None, description="UUID of supplier Company")
    purchase_order_line_id: Optional[uuid.UUID] = Field(None, description="UUID of PurchaseOrderLineItem")
    goods_receipt_line_id: Optional[uuid.UUID] = Field(None, description="UUID of GoodsReceiptLine")
    lot_number: Optional[str] = Field(None, max_length=100, description="Lot number (auto-generated if omitted)")
    manufactured_date: Optional[date] = Field(None, description="Manufacturing date")
    expiry_date: Optional[date] = Field(None, description="Expiry date")
    unit_cost: Decimal = Field(Decimal("0"), ge=0, description="Unit cost")
    currency_code: str = Field("", max_length=10, description="Currency code")
    received_quantity: Decimal = Field(Decimal("0"), ge=0, description="Quantity received")
    remaining_quantity: Decimal = Field(Decimal("0"), ge=0, description="Remaining quantity in stock")
    status: StockLotStatus = Field(StockLotStatus.OPEN, description="Lot status")

    @field_validator("remaining_quantity")
    def remaining_le_received(cls, v, info):
        data = info.data
        if "received_quantity" in data and v > data["received_quantity"]:
            raise ValueError("Remaining quantity cannot exceed received quantity.")
        return v

# ------------------------------------------------------------------------------
# StockSerial
# ------------------------------------------------------------------------------
class StockSerialCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a StockSerial."""
    inventory_item_id: uuid.UUID = Field(..., description="UUID of InventoryItem")
    stock_lot_id: Optional[uuid.UUID] = Field(None, description="UUID of StockLot")
    stock_location_id: Optional[uuid.UUID] = Field(None, description="UUID of StockLocation")
    serial_number: str = Field(..., max_length=100, description="Serial number")
    status: StockSerialStatus = Field(StockSerialStatus.AVAILABLE, description="Serial status")

# ------------------------------------------------------------------------------
# StockBalance
# ------------------------------------------------------------------------------
class StockBalanceCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a StockBalance."""
    inventory_item_id: uuid.UUID = Field(..., description="UUID of InventoryItem")
    stock_location_id: uuid.UUID = Field(..., description="UUID of StockLocation")
    stock_lot_id: Optional[uuid.UUID] = Field(None, description="UUID of StockLot")
    quantity_on_hand: Decimal = Field(Decimal("0"), ge=0, description="Quantity on hand")
    quantity_reserved: Decimal = Field(Decimal("0"), ge=0, description="Quantity reserved")
    # quantity_available is calculated automatically

    @model_validator(mode="after")
    def reserved_le_on_hand(self):
        if self.quantity_reserved > self.quantity_on_hand:
            raise ValueError("Reserved quantity cannot exceed on-hand quantity.")
        return self

# ------------------------------------------------------------------------------
# StockReservation
# ------------------------------------------------------------------------------
class StockReservationCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a StockReservation."""
    inventory_item_id: uuid.UUID = Field(..., description="UUID of InventoryItem")
    stock_lot_id: Optional[uuid.UUID] = Field(None, description="UUID of StockLot")
    stock_serial_id: Optional[uuid.UUID] = Field(None, description="UUID of StockSerial")
    stock_location_id: uuid.UUID = Field(..., description="UUID of StockLocation")
    external_order_type: str = Field(..., max_length=50, description="Order type (e.g., SalesOrder, PurchaseOrder)")
    external_order_id: str = Field(..., max_length=100, description="Order ID/reference")
    external_order_line_id: str = Field("", max_length=100, description="Order line ID")
    reserved_quantity: Decimal = Field(..., gt=0, description="Quantity reserved")
    fulfilled_quantity: Decimal = Field(Decimal("0"), ge=0, description="Quantity already fulfilled")
    status: StockReservationStatus = Field(StockReservationStatus.ACTIVE, description="Reservation status")
    expires_at: Optional[datetime] = Field(None, description="Expiration datetime")

    @model_validator(mode="after")
    def fulfilled_le_reserved(self):
        if self.fulfilled_quantity > self.reserved_quantity:
            raise ValueError("Fulfilled quantity cannot exceed reserved quantity.")
        return self

# ------------------------------------------------------------------------------
# StockMovement
# ------------------------------------------------------------------------------
class StockMovementCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a StockMovement."""
    inventory_item_id: uuid.UUID = Field(..., description="UUID of InventoryItem")
    stock_lot_id: Optional[uuid.UUID] = Field(None, description="UUID of StockLot")
    stock_serial_id: Optional[uuid.UUID] = Field(None, description="UUID of StockSerial")
    from_location_id: Optional[uuid.UUID] = Field(None, description="UUID of source StockLocation")
    to_location_id: Optional[uuid.UUID] = Field(None, description="UUID of destination StockLocation")
    movement_type: StockMovementType = Field(..., description="Movement type")
    quantity: Decimal = Field(..., gt=0, description="Quantity moved")
    unit_cost: Optional[Decimal] = Field(None, max_digits=15, decimal_places=5, description="Unit cost")
    reference_type: str = Field("", max_length=64, description="Type of reference document")
    reference_id: str = Field("", max_length=100, description="Reference document ID")
    actor_user_id: Optional[int] = Field(None, description="User ID performing the movement")
    occurred_at: Optional[datetime] = Field(None, description="Timestamp of movement (defaults to now)")
    notes: str = Field("", description="Notes")

    @model_validator(mode="after")
    def validate_locations(self):
        if self.movement_type == StockMovementType.TRANSFER:
            if not self.from_location_id or not self.to_location_id:
                raise ValueError("Transfer requires both from_location_id and to_location_id.")
        elif self.movement_type == StockMovementType.RECEIPT:
            if not self.to_location_id:
                raise ValueError("Receipt requires to_location_id.")
        elif self.movement_type == StockMovementType.ISSUE:
            if not self.from_location_id:
                raise ValueError("Issue requires from_location_id.")
        # Adjustments, reservations, releases may not require locations
        return self

# ------------------------------------------------------------------------------
# StockAdjustment
# ------------------------------------------------------------------------------
class StockAdjustmentCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a StockAdjustment."""
    inventory_item_id: uuid.UUID = Field(..., description="UUID of InventoryItem")
    adjustment_type: AdjustmentType = Field(..., description="Adjustment type")
    quantity_change: int = Field(..., description="Change in quantity (positive for add, negative for remove, can be any for transfer)")
    reason: Optional[str] = Field(None, description="Reason for adjustment")
    adjusted_by: Optional[str] = Field(None, max_length=255, description="Username of adjuster")
    adjusted_by_user_id: Optional[int] = Field(None, description="User ID of adjuster")
    # adjusted_at is auto_now_add

    @field_validator("quantity_change")
    def non_zero_quantity(cls, v):
        if v == 0:
            raise ValueError("Quantity change cannot be zero.")
        return v

    @model_validator(mode="after")
    def validate_transfer_quantity(self):
        if self.adjustment_type == AdjustmentType.TRANSFER and self.quantity_change <= 0:
            raise ValueError("Transfer adjustment must have positive quantity change (will be used as moved amount).")
        if self.adjustment_type == AdjustmentType.ADD and self.quantity_change <= 0:
            raise ValueError("Add adjustment must have positive quantity change.")
        if self.adjustment_type == AdjustmentType.REMOVE and self.quantity_change >= 0:
            raise ValueError("Remove adjustment must have negative quantity change.")
        return self


class McpPayloadModel(BaseModel):
    """Shared MCP contract model that preserves known schema while tolerating backend extras."""

    model_config = ConfigDict(extra="allow")


class StockLocationInventoryTypePayload(McpPayloadModel):
    inventory_type: Optional[str] = Field(None, description="Inventory type represented at the location")
    quantity: Optional[float] = Field(None, description="Quantity held for the type")
    total_value: Optional[float] = Field(None, description="Stock value for the type")


class StockLocationResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Stock location identifier")
    name: str = Field(..., description="Stock location name")
    code: Optional[str] = Field(None, description="Stock location code")
    location_type: Optional[str] = Field(None, description="Stock location type display name")
    parent_name: Optional[str] = Field(None, description="Parent location display name")
    structural: Optional[bool] = Field(None, description="Whether the location is structural only")
    external: Optional[bool] = Field(None, description="Whether the location is external")
    physical_address: str = Field("", description="Physical address")
    description: str = Field("", description="Location description")
    total_items: int = Field(0, description="Number of stocked items")
    total_quantity: Optional[float] = Field(None, description="Total quantity at the location")
    total_value: Optional[float] = Field(None, description="Total stock value at the location")
    expiring_soon_count: int = Field(0, description="Number of expiring lots at the location")
    top_inventory_types: List[StockLocationInventoryTypePayload] = Field(
        default_factory=list,
        description="Top inventory types stored at the location",
    )


class StockLotResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Stock lot identifier")
    inventory_item_id: Optional[str] = Field(None, description="Inventory item identifier")
    inventory_item_name: Optional[str] = Field(None, description="Inventory item name")
    lot_number: str = Field(..., description="Lot number")
    expiry_date: Optional[str] = Field(None, description="ISO-8601 expiry date")
    unit_cost: Optional[float] = Field(None, description="Unit cost")
    received_quantity: Optional[float] = Field(None, description="Quantity received into the lot")
    remaining_quantity: Optional[float] = Field(None, description="Remaining quantity in the lot")
    status: Optional[str] = Field(None, description="Lot lifecycle status")


class StockSerialResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Stock serial identifier")
    inventory_item_id: Optional[str] = Field(None, description="Inventory item identifier")
    inventory_item_name: Optional[str] = Field(None, description="Inventory item name")
    stock_lot_id: Optional[str] = Field(None, description="Stock lot identifier")
    lot_number: Optional[str] = Field(None, description="Lot number")
    serial_number: str = Field(..., description="Serial number")
    status: Optional[str] = Field(None, description="Serial lifecycle status")
    stock_location_id: Optional[str] = Field(None, description="Location identifier")
    stock_location_name: Optional[str] = Field(None, description="Location display name")


class StockBalanceResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Stock balance identifier")
    inventory_item_id: str = Field(..., description="Inventory item identifier")
    inventory_item_name: str = Field(..., description="Inventory item name")
    stock_location_id: str = Field(..., description="Stock location identifier")
    stock_location_name: str = Field(..., description="Stock location name")
    stock_lot_id: Optional[str] = Field(None, description="Stock lot identifier")
    lot_number: Optional[str] = Field(None, description="Lot number")
    quantity_on_hand: Optional[float] = Field(None, description="Quantity currently on hand")
    quantity_reserved: Optional[float] = Field(None, description="Quantity currently reserved")
    quantity_available: Optional[float] = Field(None, description="Quantity currently available")


class StockReservationResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Stock reservation identifier")
    inventory_item_id: str = Field(..., description="Inventory item identifier")
    inventory_item_name: str = Field(..., description="Inventory item name")
    stock_location_id: str = Field(..., description="Stock location identifier")
    stock_location_name: str = Field(..., description="Stock location name")
    stock_lot_id: Optional[str] = Field(None, description="Reserved stock lot identifier")
    lot_number: Optional[str] = Field(None, description="Reserved lot number")
    stock_serial_id: Optional[str] = Field(None, description="Reserved stock serial identifier")
    serial_number: Optional[str] = Field(None, description="Reserved serial number")
    external_order_type: str = Field(..., description="External order type")
    external_order_id: str = Field(..., description="External order identifier")
    external_order_line_id: str = Field("", description="External order line identifier")
    reserved_quantity: Optional[float] = Field(None, description="Reserved quantity")
    fulfilled_quantity: Optional[float] = Field(None, description="Fulfilled quantity")
    remaining_quantity: Optional[float] = Field(None, description="Remaining quantity")
    status: Optional[str] = Field(None, description="Reservation status")
    expires_at: Optional[str] = Field(None, description="Reservation expiry timestamp")
    created_at: Optional[str] = Field(None, description="Reservation creation timestamp")


class StockMovementResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Stock movement identifier")
    inventory_item_id: str = Field(..., description="Inventory item identifier")
    inventory_item_name: str = Field(..., description="Inventory item name")
    movement_type: str = Field(..., description="Movement type")
    quantity: Optional[float] = Field(None, description="Movement quantity")
    unit_cost: Optional[float] = Field(None, description="Movement unit cost")
    from_location_id: Optional[str] = Field(None, description="Source location identifier")
    from_location_name: Optional[str] = Field(None, description="Source location name")
    to_location_id: Optional[str] = Field(None, description="Destination location identifier")
    to_location_name: Optional[str] = Field(None, description="Destination location name")
    stock_lot_id: Optional[str] = Field(None, description="Stock lot identifier")
    lot_number: Optional[str] = Field(None, description="Lot number")
    stock_serial_id: Optional[str] = Field(None, description="Stock serial identifier")
    serial_number: Optional[str] = Field(None, description="Serial number")
    reference_type: str = Field("", description="Reference type")
    reference_id: str = Field("", description="Reference identifier")
    actor_user_id: Optional[int] = Field(None, description="Actor user identifier")
    occurred_at: Optional[str] = Field(None, description="Movement occurrence timestamp")
    notes: str = Field("", description="Movement notes")


class StockLocationCollectionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    query: Optional[str] = Field(None, description="Applied search query")
    count: int = Field(0, description="Number of returned locations")
    limit: Optional[int] = Field(None, description="Applied result limit")
    results: List[StockLocationResponsePayload] = Field(default_factory=list, description="Location results")


class StockLocationSummaryResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    location: StockLocationResponsePayload = Field(..., description="Stock location payload")


class StockReservationCollectionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    query: Optional[str] = Field(None, description="Applied search query")
    count: int = Field(0, description="Number of returned reservations")
    limit: Optional[int] = Field(None, description="Applied result limit")
    results: List[StockReservationResponsePayload] = Field(default_factory=list, description="Reservation results")


class StockLotCollectionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    query: Optional[str] = Field(None, description="Applied search query")
    count: int = Field(0, description="Number of returned lots")
    limit: Optional[int] = Field(None, description="Applied result limit")
    results: List[StockLotResponsePayload] = Field(default_factory=list, description="Stock lot results")


class StockSerialCollectionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    query: Optional[str] = Field(None, description="Applied search query")
    count: int = Field(0, description="Number of returned serials")
    limit: Optional[int] = Field(None, description="Applied result limit")
    results: List[StockSerialResponsePayload] = Field(default_factory=list, description="Stock serial results")


class StockBalanceCollectionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    query: Optional[str] = Field(None, description="Applied search query")
    count: int = Field(0, description="Number of returned balance rows")
    limit: Optional[int] = Field(None, description="Applied result limit")
    results: List[StockBalanceResponsePayload] = Field(default_factory=list, description="Stock balance results")


class StockMovementCollectionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    query: Optional[str] = Field(None, description="Applied search query")
    count: int = Field(0, description="Number of returned stock movements")
    limit: Optional[int] = Field(None, description="Applied result limit")
    results: List[StockMovementResponsePayload] = Field(default_factory=list, description="Stock movement results")


class StockReservationMutationResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    reservation: StockReservationResponsePayload = Field(..., description="Reservation mutation result")


class StockLocationMutationResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    location: StockLocationResponsePayload = Field(..., description="Location mutation result")


class StockStatusUpdatePayload(McpPayloadModel):
    status: str = Field(..., description="New stock status")
    notes: Optional[str] = Field(None, description="Status update notes")
    reason: Optional[str] = Field(None, description="Business reason for the status change")


class StockReservationActionPayload(McpPayloadModel):
    quantity: Optional[Decimal] = Field(None, description="Reservation quantity to release or fulfill")
    fulfilled_quantity: Optional[Decimal] = Field(None, description="Fulfilled quantity")
    notes: Optional[str] = Field(None, description="Operator notes")
    reason: Optional[str] = Field(None, description="Business reason")


class InventoryAdjustmentLinePayload(McpPayloadModel):
    inventory_item_id: Optional[uuid.UUID] = Field(None, description="Inventory item identifier")
    stock_lot_id: Optional[uuid.UUID] = Field(None, description="Stock lot identifier")
    stock_serial_id: Optional[uuid.UUID] = Field(None, description="Stock serial identifier")
    stock_location_id: Optional[uuid.UUID] = Field(None, description="Stock location identifier")
    quantity: Optional[Decimal] = Field(None, description="Adjustment quantity")
    unit_cost: Optional[Decimal] = Field(None, description="Unit cost")
    adjustment_type: Optional[str] = Field(None, description="Adjustment type")
    notes: Optional[str] = Field(None, description="Adjustment notes")


class InventoryAdjustmentRequestPayload(McpPayloadModel):
    adjustments: List[InventoryAdjustmentLinePayload] = Field(default_factory=list, description="Inventory adjustment lines")
    notes: Optional[str] = Field(None, description="Adjustment notes")
    reason: Optional[str] = Field(None, description="Business reason for the adjustment")


class StockTransferLinePayload(McpPayloadModel):
    inventory_item_id: Optional[uuid.UUID] = Field(None, description="Inventory item identifier")
    stock_lot_id: Optional[uuid.UUID] = Field(None, description="Stock lot identifier")
    stock_serial_id: Optional[uuid.UUID] = Field(None, description="Stock serial identifier")
    from_location_id: Optional[uuid.UUID] = Field(None, description="Source location identifier")
    to_location_id: Optional[uuid.UUID] = Field(None, description="Destination location identifier")
    quantity: Optional[Decimal] = Field(None, description="Transfer quantity")
    unit_cost: Optional[Decimal] = Field(None, description="Transfer unit cost")
    notes: Optional[str] = Field(None, description="Transfer notes")


class StockTransferRequestPayload(McpPayloadModel):
    transfers: List[StockTransferLinePayload] = Field(default_factory=list, description="Stock transfer lines")
    notes: Optional[str] = Field(None, description="Transfer notes")
    reason: Optional[str] = Field(None, description="Business reason for the transfer")


class StockAdjustmentResponsePayload(McpPayloadModel):
    message: str = Field("", description="Adjustment result message")
    old_quantity: Optional[Decimal] = Field(None, description="Quantity before adjustment")
    new_quantity: Optional[Decimal] = Field(None, description="Quantity after adjustment")
    change: Optional[Decimal] = Field(None, description="Applied quantity delta")


class StockAdjustmentResultPayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    inventory_adjustment: StockAdjustmentResponsePayload = Field(..., description="Inventory adjustment result")


class StockTransferResponsePayload(McpPayloadModel):
    message: str = Field("", description="Transfer result message")
    transferred_quantity: Optional[Decimal] = Field(None, description="Transferred quantity")
    from_location: str = Field("", description="Source location name")
    to_location: str = Field("", description="Destination location name")


class StockTransferResultPayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    stock_transfer: StockTransferResponsePayload = Field(..., description="Stock transfer result")


class InventoryItemDetailResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    inventory_item: InventoryItemResponsePayload = Field(..., description="Inventory item payload")
    balances: List[StockBalanceResponsePayload] = Field(default_factory=list, description="Location-level stock balances")
    lots: List[StockLotResponsePayload] = Field(default_factory=list, description="Related stock lots")
    serials: List[StockSerialResponsePayload] = Field(default_factory=list, description="Related stock serials")
    active_reservations: List[StockReservationResponsePayload] = Field(
        default_factory=list,
        description="Active reservations",
    )
    recent_movements: List[StockMovementResponsePayload] = Field(
        default_factory=list,
        description="Recent stock movements",
    )


class InventoryItemStatusResultPayload(McpPayloadModel):
    message: str = Field("", description="Status update result message")
    old_status: Optional[str] = Field(None, description="Previous inventory-item status")
    new_status: Optional[str] = Field(None, description="Updated inventory-item status")


class InventoryItemActionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    inventory_item: (
        InventoryItemResponsePayload
        | List[InventoryItemResponsePayload]
        | List[StockMovementResponsePayload]
        | InventoryItemStatusResultPayload
    ) = Field(
        ...,
        description="Inventory-item action result",
    )
