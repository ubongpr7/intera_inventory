from decimal import Decimal
from enum import Enum
import uuid
from datetime import date, datetime
from typing import Optional, Literal, Dict, Any, List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ------------------------------------------------------------------------------
# Enumerations (mirror Django choices)
# ------------------------------------------------------------------------------

class PurchaseOrderStatus(str, Enum):
    PENDING = "pending"
    ISSUED = "issued"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OVERDUE = "overdue"
    RECEIVED = "received"
    REJECTED = "rejected"
    APPROVED = "approved"
    LOST = "lost"
    RETURNED = "returned"

class SalesOrderStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class ReturnOrderStatus(str, Enum):
    PENDING = "pending"
    AWAITING_PICKUP = "awaiting_pickup"
    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class WorkflowState(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SENT_TO_SUPPLIER = "SENT_TO_SUPPLIER"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    FULLY_RECEIVED = "FULLY_RECEIVED"
    CLOSED = "CLOSED"

# ------------------------------------------------------------------------------
# PurchaseOrderLineItem
# ------------------------------------------------------------------------------
class PurchaseOrderLineItemCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a PurchaseOrderLineItem."""
    purchase_order_id: uuid.UUID = Field(..., description="UUID of the PurchaseOrder")
    inventory_item_id: uuid.UUID = Field(..., description="UUID of InventoryItem")
    quantity: int = Field(..., gt=0, description="Ordered quantity")
    quantity_received: Decimal = Field(Decimal("0"), description="Cumulative quantity received so far")
    unit_price: Decimal = Field(..., gt=0, description="Unit price")
    discount_rate: Decimal = Field(Decimal("0"), ge=0, le=100, description="Discount percentage")
    tax_rate: Decimal = Field(Decimal("0"), ge=0, le=100, description="Tax percentage")
    description: Optional[str] = Field(None, description="Line item description")
    expiry_date: Optional[date] = Field(None, description="Expiry date for batch")
    manufactured_date: Optional[date] = Field(None, description="Manufacturing date")
    batch_number: Optional[str] = Field(None, max_length=30, description="Batch/lot number (auto-generated if not provided)")

    @field_validator("quantity_received")
    def non_negative_received(cls, v):
        if v < 0:
            raise ValueError("Quantity received cannot be negative")
        return v

    @model_validator(mode="after")
    def validate_quantity_received(self):
        if self.quantity_received > self.quantity:
            raise ValueError("Quantity received cannot exceed ordered quantity.")
        return self

# ------------------------------------------------------------------------------
# PurchaseOrder
# ------------------------------------------------------------------------------
class PurchaseOrderCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a PurchaseOrder."""
    reference: Optional[str] = Field(None, max_length=64, description="Order reference (auto-generated if omitted)")
    status: PurchaseOrderStatus = Field(PurchaseOrderStatus.PENDING, description="Order status")
    supplier_id: Optional[uuid.UUID] = Field(None, description="UUID of supplier Company")
    supplier_reference: Optional[str] = Field(None, max_length=64, description="Supplier's reference code")
    received_by: Optional[str] = Field(None, max_length=400, description="User who received the goods")
    received_by_user_id: Optional[int] = Field(None, description="User ID of receiver")
    issue_date: Optional[datetime] = Field(None, description="Date order was issued")
    complete_date: Optional[datetime] = Field(None, description="Date order was completed")
    workflow_state: WorkflowState = Field(WorkflowState.DRAFT, description="Workflow state")
    approval_required: bool = Field(False, description="Whether approval is required")
    approved_by: Optional[str] = Field(None, max_length=255, description="Approver name")
    approved_by_user_id: Optional[int] = Field(None, description="Approver user ID")
    approved_at: Optional[datetime] = Field(None, description="Approval timestamp")
    budget_code: Optional[str] = Field(None, max_length=50, description="Budget code")
    department: Optional[str] = Field(None, max_length=100, description="Department")
    # Fields from Order abstract base
    description: Optional[str] = Field(None, max_length=250, description="Order description")
    notes: Optional[str] = Field(None, description="Additional notes")
    link: Optional[str] = Field(None, description="External link")
    delivery_date: Optional[date] = Field(None, description="Expected delivery date")
    received_date: Optional[date] = Field(None, description="Date received")
    responsible: Optional[str] = Field(None, max_length=400, description="Responsible user/group")
    responsible_user_id: Optional[int] = Field(None, description="Responsible user ID")
    contact_id: Optional[uuid.UUID] = Field(None, description="UUID of Contact person")
    address_id: Optional[uuid.UUID] = Field(None, description="UUID of CompanyAddress")
    order_currency: Optional[str] = Field(None, max_length=10, description="Currency code")

    @field_validator("reference")
    def validate_reference(cls, v):
        # If provided, it must be unique and follow some pattern? We'll skip for now.
        return v

# ------------------------------------------------------------------------------
# GoodsReceipt
# ------------------------------------------------------------------------------
class GoodsReceiptCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a GoodsReceipt."""
    reference: Optional[str] = Field(None, max_length=64, description="Auto-generated if omitted")
    purchase_order_id: Optional[uuid.UUID] = Field(None, description="UUID of PurchaseOrder")
    supplier_id: Optional[uuid.UUID] = Field(None, description="UUID of supplier Company")
    received_at: Optional[datetime] = Field(None, description="Receipt timestamp (defaults to now)")
    received_by_user_id: Optional[int] = Field(None, description="User ID of receiver")
    notes: Optional[str] = Field(None, description="Additional notes")

# ------------------------------------------------------------------------------
# GoodsReceiptLine
# ------------------------------------------------------------------------------
class GoodsReceiptLineCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a GoodsReceiptLine."""
    goods_receipt_id: uuid.UUID = Field(..., description="UUID of GoodsReceipt")
    purchase_order_line_id: Optional[uuid.UUID] = Field(None, description="UUID of PurchaseOrderLineItem (if applicable)")
    inventory_item_id: uuid.UUID = Field(..., description="UUID of InventoryItem received")
    stock_location_id: uuid.UUID = Field(..., description="UUID of StockLocation where goods are placed")
    received_quantity: Decimal = Field(..., gt=0, description="Quantity received")
    unit_cost: Decimal = Field(..., ge=0, description="Unit cost")
    lot_number: Optional[str] = Field(None, max_length=100, description="Lot number")
    manufactured_date: Optional[date] = Field(None, description="Manufacturing date")
    expiry_date: Optional[date] = Field(None, description="Expiry date")

# ------------------------------------------------------------------------------
# SalesOrder
# ------------------------------------------------------------------------------
class SalesOrderCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a SalesOrder."""
    reference: Optional[str] = Field(None, max_length=64, description="Sales order reference (auto-generated)")
    status: SalesOrderStatus = Field(SalesOrderStatus.PENDING, description="Order status")
    customer_id: Optional[uuid.UUID] = Field(None, description="UUID of customer Company")
    customer_reference: Optional[str] = Field(None, max_length=64, description="Customer's reference code")
    issue_date: Optional[datetime] = Field(None, description="Date order was issued")
    shipment_date: Optional[datetime] = Field(None, description="Expected shipment date")
    shipped_by: Optional[str] = Field(None, max_length=400, description="User/group responsible for shipping")
    shipped_by_user_id: Optional[int] = Field(None, description="User ID of shipper")
    # Fields from Order abstract base
    description: Optional[str] = Field(None, max_length=250, description="Order description")
    notes: Optional[str] = Field(None, description="Additional notes")
    link: Optional[str] = Field(None, description="External link")
    delivery_date: Optional[date] = Field(None, description="Expected delivery date")
    received_date: Optional[date] = Field(None, description="Date received")
    responsible: Optional[str] = Field(None, max_length=400, description="Responsible user/group")
    responsible_user_id: Optional[int] = Field(None, description="Responsible user ID")
    contact_id: Optional[uuid.UUID] = Field(None, description="UUID of Contact person")
    address_id: Optional[uuid.UUID] = Field(None, description="UUID of CompanyAddress")
    order_currency: Optional[str] = Field(None, max_length=10, description="Currency code")

# ------------------------------------------------------------------------------
# SalesOrderLineItem
# ------------------------------------------------------------------------------
class SalesOrderLineItemCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a SalesOrderLineItem."""
    sales_order_id: uuid.UUID = Field(..., description="UUID of SalesOrder")
    inventory_item_id: uuid.UUID = Field(..., description="UUID of InventoryItem")
    quantity: Decimal = Field(..., gt=0, description="Ordered quantity")
    reserved_quantity: Decimal = Field(Decimal("0"), ge=0, description="Quantity currently reserved")
    shipped_quantity: Decimal = Field(Decimal("0"), ge=0, description="Quantity already shipped")
    unit_price: Decimal = Field(..., ge=0, description="Unit price")
    discount_rate: Decimal = Field(Decimal("0"), ge=0, le=100, description="Discount percentage")
    tax_rate: Decimal = Field(Decimal("0"), ge=0, le=100, description="Tax percentage")
    description: Optional[str] = Field(None, description="Line item description")

    @model_validator(mode="after")
    def validate_reserved_and_shipped(self):
        if self.reserved_quantity > self.quantity:
            raise ValueError("Reserved quantity cannot exceed ordered quantity.")
        if self.shipped_quantity > self.quantity:
            raise ValueError("Shipped quantity cannot exceed ordered quantity.")
        if self.reserved_quantity + self.shipped_quantity > self.quantity:
            raise ValueError("Reserved and shipped quantities combined cannot exceed ordered quantity.")
        return self

# ------------------------------------------------------------------------------
# SalesOrderShipment
# ------------------------------------------------------------------------------
class SalesOrderShipmentCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a SalesOrderShipment."""
    order_id: uuid.UUID = Field(..., description="UUID of SalesOrder")
    shipment_date: Optional[date] = Field(None, description="Date of shipment")
    delivery_date: Optional[date] = Field(None, description="Date of delivery")
    checked_by: Optional[str] = Field(None, max_length=400, description="User who checked the shipment")
    checked_by_user_id: Optional[int] = Field(None, description="User ID of checker")
    reference: Optional[str] = Field(None, max_length=100, description="Shipment reference (auto-generated)")
    tracking_number: Optional[str] = Field(None, max_length=100, description="Tracking number")
    invoice_number: Optional[str] = Field(None, max_length=100, description="Invoice number")
    link: Optional[str] = Field(None, description="External link")
    notes: Optional[str] = Field(None, description="Shipment notes")
    @model_validator(mode="after")
    def validate_reference_uniqueness(self):
        # We can't easily validate uniqueness here; it's left to the database.
        return self

# ------------------------------------------------------------------------------
# SalesOrderShipmentLine
# ------------------------------------------------------------------------------
class SalesOrderShipmentLineCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a SalesOrderShipmentLine."""
    shipment_id: uuid.UUID = Field(..., description="UUID of SalesOrderShipment")
    sales_order_line_id: uuid.UUID = Field(..., description="UUID of SalesOrderLineItem")
    stock_location_id: uuid.UUID = Field(..., description="UUID of StockLocation from which stock is taken")
    stock_lot_id: Optional[uuid.UUID] = Field(None, description="UUID of StockLot (if lot-tracked)")
    stock_serial_id: Optional[uuid.UUID] = Field(None, description="UUID of StockSerial (if serial-tracked)")
    reservation_id: Optional[uuid.UUID] = Field(None, description="UUID of StockReservation")
    quantity_shipped: Decimal = Field(..., gt=0, description="Quantity shipped in this line")
    notes: Optional[str] = Field(None, description="Notes")

# ------------------------------------------------------------------------------
# ReturnOrder
# ------------------------------------------------------------------------------
class ReturnOrderCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a ReturnOrder."""
    reference: Optional[str] = Field(None, max_length=64, description="Return order reference (auto-generated)")
    purchase_order_id: Optional[uuid.UUID] = Field(None, description="UUID of PurchaseOrder (if applicable)")
    customer_id: Optional[uuid.UUID] = Field(None, description="UUID of customer Company")
    status: ReturnOrderStatus = Field(ReturnOrderStatus.PENDING, description="Return order status")
    customer_reference: Optional[str] = Field(None, max_length=64, description="Customer's reference code")
    issue_date: Optional[datetime] = Field(None, description="Date order was issued")
    return_reason: Optional[str] = Field(None, description="Reason for return")
    # Fields from Order abstract base
    description: Optional[str] = Field(None, max_length=250, description="Order description")
    notes: Optional[str] = Field(None, description="Additional notes")
    link: Optional[str] = Field(None, description="External link")
    delivery_date: Optional[date] = Field(None, description="Expected delivery date")
    received_date: Optional[date] = Field(None, description="Date received")
    responsible: Optional[str] = Field(None, max_length=400, description="Responsible user/group")
    responsible_user_id: Optional[int] = Field(None, description="Responsible user ID")
    contact_id: Optional[uuid.UUID] = Field(None, description="UUID of Contact person")
    address_id: Optional[uuid.UUID] = Field(None, description="UUID of CompanyAddress")
    order_currency: Optional[str] = Field(None, max_length=10, description="Currency code")

# ------------------------------------------------------------------------------
# ReturnOrderLineItem
# ------------------------------------------------------------------------------
class ReturnOrderLineItemCreateUpdatePayload(BaseModel):
    """Payload for creating/updating a ReturnOrderLineItem."""
    return_order_id: uuid.UUID = Field(..., description="UUID of ReturnOrder")
    original_line_item_id: uuid.UUID = Field(..., description="UUID of PurchaseOrderLineItem being returned")
    quantity_returned: int = Field(..., gt=0, description="Quantity returned")
    quantity_processed: Decimal = Field(Decimal("0"), ge=0, description="Quantity already processed (restocked/credited)")
    return_reason: Optional[str] = Field(None, description="Reason for return for this line")
    unit_price: Decimal = Field(..., ge=0, description="Unit price at time of return")
    tax_rate: Decimal = Field(Decimal("0"), ge=0, le=100, description="Tax rate")
    discount: Decimal = Field(Decimal("0"), ge=0, description="Discount amount for this line")

    @field_validator("quantity_processed")
    def non_negative_processed(cls, v):
        if v < 0:
            raise ValueError("Processed quantity cannot be negative")
        return v

    @model_validator(mode="after")
    def validate_quantity_processed(self):
        if self.quantity_processed > self.quantity_returned:
            raise ValueError("Processed quantity cannot exceed returned quantity.")
        return self

    @model_validator(mode="after")
    def validate_returnable_quantity(self):
        # This validation would require checking against original line's received quantity minus already returned.
        # We can't do that in a payload validator because it requires DB access. We'll skip and rely on DB.
        return self


class McpPayloadModel(BaseModel):
    """Shared MCP contract model that preserves known schema while tolerating backend extras."""

    model_config = ConfigDict(extra="allow")


class OrderLineItemResponsePayload(McpPayloadModel):
    id: Optional[str] = Field(None, description="Order line identifier")
    inventory_item_id: Optional[str] = Field(None, description="Inventory item identifier")
    quantity: Optional[float] = Field(None, description="Ordered quantity")
    quantity_received: Optional[float] = Field(None, description="Received quantity")
    reserved_quantity: Optional[float] = Field(None, description="Reserved quantity")
    shipped_quantity: Optional[float] = Field(None, description="Shipped quantity")
    quantity_returned: Optional[float] = Field(None, description="Returned quantity")
    quantity_processed: Optional[float] = Field(None, description="Processed return quantity")
    unit_price: Optional[float] = Field(None, description="Unit price")
    discount_rate: Optional[float] = Field(None, description="Discount percentage")
    tax_rate: Optional[float] = Field(None, description="Tax percentage")
    description: Optional[str] = Field(None, description="Order line description")
    batch_number: Optional[str] = Field(None, description="Batch number")
    expiry_date: Optional[str] = Field(None, description="Expiry date")
    manufactured_date: Optional[str] = Field(None, description="Manufactured date")


class PurchaseOrderResponsePayload(McpPayloadModel):
    id: Optional[str] = Field(None, description="Purchase order identifier")
    reference: Optional[str] = Field(None, description="Purchase order reference")
    status: Optional[str] = Field(None, description="Purchase order status")
    workflow_state: Optional[str] = Field(None, description="Purchase order workflow state")
    supplier_id: Optional[str] = Field(None, description="Supplier identifier")
    supplier_reference: Optional[str] = Field(None, description="Supplier reference")
    description: Optional[str] = Field(None, description="Purchase order description")
    notes: Optional[str] = Field(None, description="Purchase order notes")
    issue_date: Optional[str] = Field(None, description="Issue timestamp")
    delivery_date: Optional[str] = Field(None, description="Delivery date")
    received_date: Optional[str] = Field(None, description="Received date")
    complete_date: Optional[str] = Field(None, description="Completion timestamp")
    approval_required: Optional[bool] = Field(None, description="Whether approval is required")


class SalesOrderResponsePayload(McpPayloadModel):
    id: Optional[str] = Field(None, description="Sales order identifier")
    reference: Optional[str] = Field(None, description="Sales order reference")
    status: Optional[str] = Field(None, description="Sales order status")
    customer_id: Optional[str] = Field(None, description="Customer identifier")
    customer_reference: Optional[str] = Field(None, description="Customer reference")
    description: Optional[str] = Field(None, description="Sales order description")
    notes: Optional[str] = Field(None, description="Sales order notes")
    issue_date: Optional[str] = Field(None, description="Issue timestamp")
    shipment_date: Optional[str] = Field(None, description="Shipment timestamp")
    delivery_date: Optional[str] = Field(None, description="Delivery date")
    received_date: Optional[str] = Field(None, description="Received date")


class ReturnOrderResponsePayload(McpPayloadModel):
    id: Optional[str] = Field(None, description="Return order identifier")
    reference: Optional[str] = Field(None, description="Return order reference")
    status: Optional[str] = Field(None, description="Return order status")
    purchase_order_id: Optional[str] = Field(None, description="Related purchase order identifier")
    customer_id: Optional[str] = Field(None, description="Customer identifier")
    customer_reference: Optional[str] = Field(None, description="Customer reference")
    return_reason: Optional[str] = Field(None, description="Return reason")
    description: Optional[str] = Field(None, description="Return order description")
    notes: Optional[str] = Field(None, description="Return order notes")
    issue_date: Optional[str] = Field(None, description="Issue timestamp")
    delivery_date: Optional[str] = Field(None, description="Delivery date")
    received_date: Optional[str] = Field(None, description="Received date")


class BinaryExportPayload(McpPayloadModel):
    content_type: str = Field(..., description="Export MIME type")
    filename: Optional[str] = Field(None, description="Suggested filename")
    size: int = Field(..., description="Encoded payload size in bytes")
    base64: str = Field(..., description="Base64-encoded file content")


class OrderActionResultPayload(McpPayloadModel):
    message: str = Field("", description="Human-readable action result")
    status: Optional[str] = Field(None, description="Updated workflow status when provided")
    order_status: Optional[str] = Field(None, description="Order status returned by some workflow actions")
    approved_at: Optional[str] = Field(None, description="Approval timestamp")
    issue_date: Optional[str] = Field(None, description="Issue timestamp")
    received_date: Optional[str] = Field(None, description="Receiving timestamp")
    completion_date: Optional[str] = Field(None, description="Completion timestamp")
    cancelled_at: Optional[str] = Field(None, description="Cancellation timestamp")
    total_price: Optional[Decimal] = Field(None, description="Calculated order value when returned")
    email_sent: Optional[bool] = Field(None, description="Whether a notification email was sent")
    received_count: Optional[int] = Field(None, description="Count of received line items")
    goods_receipt_reference: Optional[str] = Field(None, description="Generated goods-receipt reference")
    return_order_reference: Optional[str] = Field(None, description="Generated return-order reference")
    return_order_id: Optional[str] = Field(None, description="Generated return-order identifier")
    processed_count: Optional[int] = Field(None, description="Count of processed return-order lines")


class SalesOrderShipmentLineResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Shipment-line identifier")
    sales_order_line: Optional[str] = Field(None, description="Sales-order line-item identifier")
    inventory_name: str = Field("", description="Inventory name")
    stock_location: Optional[str] = Field(None, description="Stock-location identifier")
    location_name: str = Field("", description="Stock-location name")
    stock_lot: Optional[str] = Field(None, description="Stock-lot identifier")
    lot_number: str = Field("", description="Lot number")
    stock_serial: Optional[str] = Field(None, description="Stock-serial identifier")
    serial_number: str = Field("", description="Serial number")
    reservation_id: Optional[str] = Field(None, description="Reservation identifier")
    quantity_shipped: Optional[Decimal] = Field(None, description="Shipped quantity")
    notes: str = Field("", description="Shipment notes")
    created_at: Optional[str] = Field(None, description="Creation timestamp")


class SalesOrderShipmentResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Shipment identifier")
    order: Optional[str] = Field(None, description="Sales-order identifier")
    reference: str = Field("", description="Shipment reference")
    shipment_date: Optional[str] = Field(None, description="Shipment date")
    delivery_date: Optional[str] = Field(None, description="Delivery date")
    checked_by: Optional[str] = Field(None, description="Checked-by user identifier")
    checked_by_user_id: Optional[str] = Field(None, description="Checked-by user identifier")
    checked_by_details: Optional[Dict[str, Any]] = Field(None, description="Resolved checked-by user details")
    tracking_number: str = Field("", description="Tracking number")
    invoice_number: str = Field("", description="Invoice number")
    link: str = Field("", description="Tracking or document link")
    notes: str = Field("", description="Shipment notes")
    lines: List[SalesOrderShipmentLineResponsePayload] = Field(default_factory=list, description="Shipment lines")
    created_at: Optional[str] = Field(None, description="Creation timestamp")
    updated_at: Optional[str] = Field(None, description="Update timestamp")


class PurchaseOrderPagePayload(McpPayloadModel):
    count: Optional[int] = Field(None, description="Total number of matching records")
    next: Optional[str] = Field(None, description="Next page URL")
    previous: Optional[str] = Field(None, description="Previous page URL")
    results: List[PurchaseOrderResponsePayload] = Field(default_factory=list, description="Serialized purchase-order records")


class SalesOrderPagePayload(McpPayloadModel):
    count: Optional[int] = Field(None, description="Total number of matching records")
    next: Optional[str] = Field(None, description="Next page URL")
    previous: Optional[str] = Field(None, description="Previous page URL")
    results: List[SalesOrderResponsePayload] = Field(default_factory=list, description="Serialized sales-order records")


class ReturnOrderPagePayload(McpPayloadModel):
    count: Optional[int] = Field(None, description="Total number of matching records")
    next: Optional[str] = Field(None, description="Next page URL")
    previous: Optional[str] = Field(None, description="Previous page URL")
    results: List[ReturnOrderResponsePayload] = Field(default_factory=list, description="Serialized return-order records")


class PurchaseOrderAnalyticsTrendPayload(McpPayloadModel):
    month: Optional[str] = Field(None, description="Monthly bucket label")
    week: Optional[str] = Field(None, description="Weekly bucket label")
    count: int = Field(0, description="Orders in the bucket")
    total_value: Optional[Decimal] = Field(None, description="Total order value in the bucket")


class PurchaseOrderSupplierPerformancePayload(McpPayloadModel):
    supplier_name: str = Field(..., validation_alias="supplier__name", description="Supplier name")
    order_count: int = Field(0, description="Orders attributed to the supplier")
    total_value: Optional[Decimal] = Field(None, description="Total supplier order value")
    avg_delivery_time: Optional[str] = Field(None, description="Average delivery time duration")
    on_time_deliveries: int = Field(0, description="Count of on-time deliveries")


class PurchaseOrderTopSupplierPayload(McpPayloadModel):
    supplier_id: str = Field(..., validation_alias="supplier__id", description="Supplier identifier")
    supplier_name: str = Field(..., validation_alias="supplier__name", description="Supplier name")
    order_count: int = Field(0, description="Orders attributed to the supplier")
    total_value: Optional[Decimal] = Field(None, description="Total supplier order value")


class PurchaseOrderStatusDistributionPayload(McpPayloadModel):
    pending: int = Field(0, description="Pending-order count")
    approved: int = Field(0, description="Approved-order count")
    issued: int = Field(0, description="Issued-order count")
    received: int = Field(0, description="Received-order count")
    completed: int = Field(0, description="Completed-order count")
    cancelled: int = Field(0, description="Cancelled-order count")


class PurchaseOrderAnalyticsPayload(McpPayloadModel):
    total_purchase_orders: int = Field(0, description="Total purchase orders")
    pending_orders: int = Field(0, description="Pending-order count")
    approved_orders: int = Field(0, description="Approved-order count")
    issued_orders: int = Field(0, description="Issued-order count")
    received_orders: int = Field(0, description="Received-order count")
    completed_orders: int = Field(0, description="Completed-order count")
    cancelled_orders: int = Field(0, description="Cancelled-order count")
    total_order_value: Optional[Decimal] = Field(None, description="Aggregate order value")
    average_order_value: Optional[Decimal] = Field(None, description="Average order value")
    monthly_trends: List[PurchaseOrderAnalyticsTrendPayload] = Field(default_factory=list, description="Monthly order trends")
    weekly_trends: List[PurchaseOrderAnalyticsTrendPayload] = Field(default_factory=list, description="Weekly order trends")
    supplier_performance: List[PurchaseOrderSupplierPerformancePayload] = Field(
        default_factory=list,
        description="Supplier-performance analytics",
    )
    top_suppliers_by_value: List[PurchaseOrderTopSupplierPayload] = Field(
        default_factory=list,
        description="Top suppliers by order value",
    )
    status_distribution: PurchaseOrderStatusDistributionPayload = Field(
        default_factory=PurchaseOrderStatusDistributionPayload,
        description="Order status distribution",
    )
    average_processing_time: float = Field(0, description="Average processing time in days")
    average_delivery_time: float = Field(0, description="Average delivery time in days")
    on_time_delivery_rate: float = Field(0, description="On-time delivery rate percentage")
    total_savings: Optional[Decimal] = Field(None, description="Computed savings total")
    cost_per_order: Optional[Decimal] = Field(None, description="Average cost per order")


class PurchaseOrderSearchResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    query: Optional[str] = Field(None, description="Applied search query")
    status: Optional[str] = Field(None, description="Applied order status filter")
    results: PurchaseOrderPagePayload = Field(..., description="Paged purchase-order results")


class PurchaseOrderDetailResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    purchase_order: PurchaseOrderResponsePayload = Field(..., description="Purchase order detail payload")


class PurchaseOrderAnalyticsResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    analytics: PurchaseOrderAnalyticsPayload = Field(..., description="Purchase-order analytics payload")


class PurchaseOrderActionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    purchase_order: PurchaseOrderResponsePayload | OrderActionResultPayload = Field(
        ...,
        description="Purchase-order detail or workflow-action result",
    )


class SalesOrderSearchResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    query: Optional[str] = Field(None, description="Applied search query")
    status: Optional[str] = Field(None, description="Applied sales-order status filter")
    results: SalesOrderPagePayload = Field(..., description="Paged sales-order results")


class SalesOrderDetailResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    sales_order: SalesOrderResponsePayload = Field(..., description="Sales order detail payload")


class SalesOrderActionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    sales_order: SalesOrderResponsePayload | SalesOrderShipmentResponsePayload | OrderActionResultPayload = Field(
        ...,
        description="Sales-order detail, shipment payload, or workflow-action result",
    )


class ReturnOrderSearchResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    query: Optional[str] = Field(None, description="Applied search query")
    status: Optional[str] = Field(None, description="Applied return-order status filter")
    results: ReturnOrderPagePayload = Field(..., description="Paged return-order results")


class ReturnOrderDetailResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    return_order: ReturnOrderResponsePayload = Field(..., description="Return order detail payload")


class ReturnOrderActionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    return_order: ReturnOrderResponsePayload | OrderActionResultPayload = Field(
        ...,
        description="Return-order detail or workflow-action result",
    )


class PurchaseOrderLineItemsResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    purchase_order: List[OrderLineItemResponsePayload] | OrderLineItemResponsePayload | OrderActionResultPayload = Field(
        ...,
        description="Purchase-order line-item list, single line item, or action result",
    )


class SalesOrderLineItemsResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    sales_order: (
        List[OrderLineItemResponsePayload]
        | OrderLineItemResponsePayload
        | List[SalesOrderShipmentResponsePayload]
        | SalesOrderShipmentResponsePayload
        | OrderActionResultPayload
    ) = Field(
        ...,
        description="Sales-order line items, shipments, or action result",
    )


class PurchaseOrderAdminResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    purchase_order: BinaryExportPayload | OrderActionResultPayload = Field(
        ...,
        description="Purchase-order export or admin action payload",
    )


class OrderActionPayload(McpPayloadModel):
    notes: Optional[str] = Field(None, description="Operator notes")
    reason: Optional[str] = Field(None, description="Business reason")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional action metadata")


class PurchaseOrderReceiveItemPayload(McpPayloadModel):
    line_item_id: Optional[uuid.UUID] = Field(None, description="Purchase-order line-item identifier")
    inventory_item_id: Optional[uuid.UUID] = Field(None, description="Inventory item identifier")
    quantity_received: Optional[Decimal] = Field(None, description="Quantity received")
    unit_cost: Optional[Decimal] = Field(None, description="Unit cost")
    stock_location_id: Optional[uuid.UUID] = Field(None, description="Stock location identifier")
    lot_number: Optional[str] = Field(None, description="Lot number")
    manufactured_date: Optional[date] = Field(None, description="Manufactured date")
    expiry_date: Optional[date] = Field(None, description="Expiry date")
    notes: Optional[str] = Field(None, description="Line-item notes")


class PurchaseOrderReceiveItemsPayload(McpPayloadModel):
    items: List[PurchaseOrderReceiveItemPayload] = Field(default_factory=list, description="Items to receive")
    notes: Optional[str] = Field(None, description="Receipt notes")


class PurchaseOrderLineItemActionPayload(McpPayloadModel):
    line_item_id: Optional[uuid.UUID] = Field(None, description="Purchase-order line-item identifier")
    inventory_item_id: Optional[uuid.UUID] = Field(None, description="Inventory item identifier")
    quantity: Optional[Decimal] = Field(None, description="Ordered quantity")
    quantity_received: Optional[Decimal] = Field(None, description="Received quantity")
    unit_price: Optional[Decimal] = Field(None, description="Unit price")
    discount_rate: Optional[Decimal] = Field(None, description="Discount rate")
    tax_rate: Optional[Decimal] = Field(None, description="Tax rate")
    description: Optional[str] = Field(None, description="Line-item description")
    expiry_date: Optional[date] = Field(None, description="Expiry date")
    manufactured_date: Optional[date] = Field(None, description="Manufactured date")
    batch_number: Optional[str] = Field(None, description="Batch number")


class SalesOrderLineItemActionPayload(McpPayloadModel):
    line_item_id: Optional[uuid.UUID] = Field(None, description="Sales-order line-item identifier")
    inventory_item_id: Optional[uuid.UUID] = Field(None, description="Inventory item identifier")
    quantity: Optional[Decimal] = Field(None, description="Ordered quantity")
    reserved_quantity: Optional[Decimal] = Field(None, description="Reserved quantity")
    shipped_quantity: Optional[Decimal] = Field(None, description="Shipped quantity")
    unit_price: Optional[Decimal] = Field(None, description="Unit price")
    discount_rate: Optional[Decimal] = Field(None, description="Discount rate")
    tax_rate: Optional[Decimal] = Field(None, description="Tax rate")
    description: Optional[str] = Field(None, description="Line-item description")
