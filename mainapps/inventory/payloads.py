from decimal import Decimal
from enum import Enum
import uuid
from datetime import date, datetime
from typing import Optional, Literal, Dict, Any, List

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ------------------------------------------------------------------------------
# Reusable enumerations (mirror Django choices)
# ------------------------------------------------------------------------------

class ReorderStrategies(str, Enum):
    FIXED_QUANTITY = "FQ"
    FIXED_INTERVAL = "FI"
    DYNAMIC = "DY"

class ExpirePolicies(str, Enum):
    REMOVE = "0"
    RETURN_MANUFACTURER = "1"

class RecallPolicies(str, Enum):
    REMOVE = "0"
    NOTIFY_CUSTOMERS = "1"
    REPLACE_PRODUCT = "3"
    DESTROY = "4"
    REPAIR = "5"

class NearExpiryActions(str, Enum):
    DISCOUNT = "DISCOUNT"
    DONATE = "DONATE"
    DESTROY = "DESTROY"
    RETURN = "RETURN"

class ForecastMethods(str, Enum):
    SIMPLE_AVERAGE = "SA"
    MOVING_AVERAGE = "MA"
    EXP_SMOOTHING = "ES"

class InventoryType(str, Enum):
    RAW_MATERIAL = "raw_material"
    FINISHED_GOOD = "finished_good"
    WORK_IN_PROGRESS = "work_in_progress"
    MAINTENANCE_SPARE_PART = "maintenance_spare_part"
    CONSUMABLE = "consumable"
    TOOLING = "tooling"
    PACKAGING = "packaging"

class InventoryItemStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    DISCONTINUED = "discontinued"

class SyncStatus(str, Enum):
    SYNCED = "SYNCED"
    PENDING = "PENDING"
    ERROR = "ERROR"

class TransactionType(str, Enum):
    PO_RECEIVE = "PO_RECEIVE"
    PO_COMPLETE = "PO_COMPLETE"
    ADJUSTMENT = "ADJUSTMENT"
    SALE = "SALE"
    RETURN = "RETURN"
    LOSS = "LOSS"

# ------------------------------------------------------------------------------
# Address
# ------------------------------------------------------------------------------
class AddressCreateUpdatePayload(BaseModel):
    """Payload for creating/updating an Address."""
    country: Optional[str] = Field(None, description="Country of the address")
    region: Optional[str] = Field(None, description="Region/State")
    subregion: Optional[str] = Field(None, description="Subregion/Province")
    city: Optional[str] = Field(None, description="City")
    apt_number: Optional[int] = Field(None, description="Apartment number")
    street_number: Optional[int] = Field(None, description="Street number")
    street: Optional[str] = Field(None, description="Street name")
    postal_code: Optional[str] = Field(None, max_length=10, description="Postal code")
    latitude: Optional[Decimal] = Field(None, max_digits=9, decimal_places=6, description="Latitude")
    longitude: Optional[Decimal] = Field(None, max_digits=9, decimal_places=6, description="Longitude")

# ------------------------------------------------------------------------------
# InventoryCategory
# ------------------------------------------------------------------------------
class InventoryCategoryCreateUpdatePayload(BaseModel):
    """Payload for creating/updating an InventoryCategory."""
    name: str = Field(..., description="Category name (must be unique per tenant)")
    structural: bool = Field(False, description="If True, items cannot be directly assigned to this category")
    default_location_id: Optional[uuid.UUID] = Field(None, description="UUID of default StockLocation")
    parent_id: Optional[uuid.UUID] = Field(None, description="UUID of parent category")
    is_active: bool = Field(True, description="Whether the category is active")
    description: Optional[str] = Field(None, description="Category description")

# ------------------------------------------------------------------------------
# InventoryItem
# ------------------------------------------------------------------------------
class InventoryItemCreateUpdatePayload(BaseModel):
    """Payload for creating/updating an InventoryItem."""
    product_template_id: Optional[uuid.UUID] = Field(None, description="Associated product template UUID")
    product_variant_id: Optional[uuid.UUID] = Field(None, description="Associated product variant UUID")
    name_snapshot: str = Field(..., description="Snapshot of item name")
    sku_snapshot: str = Field("", description="Snapshot of SKU")
    barcode_snapshot: str = Field("", description="Snapshot of barcode")
    description: str = Field("", description="Item description")
    inventory_category_id: Optional[uuid.UUID] = Field(None, description="UUID of InventoryCategory")
    inventory_type: InventoryType = Field(InventoryType.RAW_MATERIAL, description="Type of inventory")
    default_uom_code: str = Field("", description="Default unit of measure code")
    stock_uom_code: str = Field("", description="Stock unit of measure code")
    track_stock: bool = Field(True, description="Enable stock tracking")
    track_lot: bool = Field(False, description="Enable lot tracking")
    track_serial: bool = Field(False, description="Enable serial tracking")
    track_expiry: bool = Field(False, description="Enable expiry tracking")
    allow_negative_stock: bool = Field(False, description="Allow negative stock balances")
    reorder_point: Decimal = Field(Decimal("0"), description="Reorder point quantity")
    reorder_quantity: Decimal = Field(Decimal("0"), description="Reorder quantity")
    minimum_stock_level: Decimal = Field(Decimal("0"), description="Minimum allowed stock")
    safety_stock_level: Decimal = Field(Decimal("0"), description="Safety stock quantity")
    default_supplier_id: Optional[uuid.UUID] = Field(None, description="UUID of default supplier (Company)")
    status: InventoryItemStatus = Field(InventoryItemStatus.ACTIVE, description="Item status")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Extra metadata")

# ------------------------------------------------------------------------------
# Inventory (central inventory model)
# ------------------------------------------------------------------------------
class InventoryCreateUpdatePayload(BaseModel):
    """Payload for creating/updating an Inventory record."""
    name: str = Field(..., description="Unique inventory name")
    description: Optional[str] = Field(None, description="Detailed description")
    category_id: Optional[uuid.UUID] = Field(None, description="UUID of InventoryCategory")
    inventory_type: InventoryType = Field(InventoryType.RAW_MATERIAL, description="Type of inventory")
    default_supplier_id: Optional[uuid.UUID] = Field(None, description="UUID of default supplier (Company)")
    is_active: bool = Field(True, description="Active status")  # from InventoryProperty
    # InventoryPolicy fields
    unit: Optional[str] = Field(None, max_length=23, description="Unit abbreviation")
    unit_name: Optional[str] = Field(None, max_length=23, description="Unit name")
    re_order_point: int = Field(10, description="Reorder point (units)")
    re_order_quantity: int = Field(200, description="Reorder quantity")
    safety_stock_level: int = Field(0, description="Safety stock level")
    minimum_stock_level: int = Field(0, description="Minimum stock level")
    supplier_lead_time: int = Field(0, description="Supplier lead time (days)")
    internal_processing_time: int = Field(1, description="Internal processing time (days)")
    reorder_strategy: ReorderStrategies = Field(ReorderStrategies.FIXED_QUANTITY, description="Replenishment strategy")
    expiration_threshold: int = Field(30, description="Days before expiry to alert")
    expiration_policy: ExpirePolicies = Field(ExpirePolicies.REMOVE, description="Expiration handling policy")
    recall_policy: RecallPolicies = Field(RecallPolicies.REMOVE, description="Recall procedure")
    near_expiry_policy: NearExpiryActions = Field(NearExpiryActions.DISCOUNT, description="Near-expiry action")
    forecast_method: ForecastMethods = Field(ForecastMethods.SIMPLE_AVERAGE, description="Demand forecasting method")
    supplier_reliability_score: Decimal = Field(Decimal("100.0"), max_digits=5, decimal_places=2, description="Supplier score (0-100)")
    alert_threshold: int = Field(10, description="Percentage variance for stock alerts")
    external_system_id: Optional[str] = Field(None, max_length=200, description="External ERP/WMS identifier")
    auto_archive_days: int = Field(365, description="Days of inactivity before archiving")
    # InventoryProperty fields
    assembly: bool = Field(False, description="Can be built from other inventory?")
    batch_tracking_enabled: bool = Field(False, description="Enable batch/lot tracking")
    automate_reorder: bool = Field(False, description="Auto-generate purchase orders")
    component: bool = Field(False, description="Can be used to build other inventory?")
    trackable: bool = Field(True, description="Enable unique item tracking?")
    testable: bool = Field(False, description="Can have test results recorded?")
    purchaseable: bool = Field(True, description="Can be purchased?")
    salable: bool = Field(True, description="Can be sold?")
    locked: bool = Field(False, description="Locked for editing?")
    virtual: bool = Field(False, description="Virtual inventory (e.g., software)?")
    # Inventory specific fields
    sync_status: SyncStatus = Field(SyncStatus.PENDING, description="Sync status with external system")
    last_sync_timestamp: Optional[datetime] = Field(None, description="Last sync timestamp")
    sync_error_message: Optional[str] = Field(None, description="Error message if sync failed")
    external_references: Dict[str, Any] = Field(default_factory=dict, description="External system references")
    officer_in_charge: Optional[str] = Field(None, max_length=400, description="Officer in charge identifier")
    officer_in_charge_user_id: Optional[int] = Field(None, description="User ID of officer in charge")

    @field_validator("minimum_stock_level", "re_order_point", "re_order_quantity", "safety_stock_level")
    def non_negative(cls, v):
        if v < 0:
            raise ValueError("Value cannot be negative")
        return v

    @field_validator("expiration_threshold", "supplier_lead_time", "internal_processing_time", "auto_archive_days")
    def positive(cls, v):
        if v < 0:
            raise ValueError("Value must be non-negative")
        return v

# ------------------------------------------------------------------------------
# InventoryBatch
# ------------------------------------------------------------------------------
class InventoryBatchCreateUpdatePayload(BaseModel):
    """Payload for creating/updating an InventoryBatch."""
    inventory_id: uuid.UUID = Field(..., description="UUID of the Inventory")
    batch_number: str = Field(..., max_length=100, description="Batch/lot number")
    manufacture_date: date = Field(..., description="Manufacture date")
    expiry_date: date = Field(..., description="Expiry date")
    quantity_received: Decimal = Field(..., max_digits=15, decimal_places=5, description="Quantity initially received")
    remaining_quantity: Decimal = Field(..., max_digits=15, decimal_places=5, description="Remaining quantity in stock")
    location_id: Optional[uuid.UUID] = Field(None, description="UUID of StockLocation where batch resides")

# ------------------------------------------------------------------------------
# InventoryTransaction
# ------------------------------------------------------------------------------
class InventoryTransactionCreateUpdatePayload(BaseModel):
    """Payload for creating/updating an InventoryTransaction."""
    item_id: uuid.UUID = Field(..., description="UUID of StockItem involved")
    quantity: int = Field(..., description="Positive for additions, negative for deductions")
    unit_price: Optional[Decimal] = Field(None, max_digits=15, decimal_places=2, description="Unit price at transaction time")
    transaction_type: TransactionType = Field(..., description="Type of transaction")
    reference: str = Field(..., max_length=64, description="Associated document number (PO, SO, etc.)")
    user: Optional[str] = Field(None, max_length=100, description="Username who performed the transaction")
    performed_by_user_id: Optional[int] = Field(None, description="User ID who performed the transaction")
    notes: Optional[str] = Field(None, description="Additional notes")


class McpPayloadModel(BaseModel):
    """Shared MCP contract model that preserves known schema while tolerating backend extras."""

    model_config = ConfigDict(extra="allow")


class InventoryLocationBreakdownPayload(McpPayloadModel):
    location_name: Optional[str] = Field(None, description="Location name in stock summaries")
    quantity: Optional[float] = Field(None, description="Quantity at the location")
    quantity_reserved: Optional[float] = Field(None, description="Reserved quantity at the location")
    quantity_available: Optional[float] = Field(None, description="Available quantity at the location")
    total_value: Optional[float] = Field(None, description="Stock value at the location")


class InventoryLotSnapshotPayload(McpPayloadModel):
    id: Optional[str] = Field(None, description="Inventory lot identifier")
    lot_number: Optional[str] = Field(None, description="Lot number")
    expiry_date: Optional[str] = Field(None, description="ISO-8601 expiry date")
    remaining_quantity: Optional[float] = Field(None, description="Remaining quantity in the lot")
    status: Optional[str] = Field(None, description="Lot lifecycle status")


class InventoryResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Inventory identifier")
    name: str = Field(..., description="Inventory name")
    external_system_id: Optional[str] = Field(None, description="External ERP/WMS identifier")
    description: str = Field("", description="Inventory description")
    inventory_type: Optional[str] = Field(None, description="Inventory type code")
    category: Optional[str] = Field(None, description="Inventory category display name")
    unit_name: Optional[str] = Field(None, description="Inventory unit name")
    active: Optional[bool] = Field(None, description="Whether the inventory is active")
    trackable: Optional[bool] = Field(None, description="Whether the inventory is trackable")
    batch_tracking_enabled: Optional[bool] = Field(None, description="Whether lot tracking is enabled")
    automate_reorder: Optional[bool] = Field(None, description="Whether reorder automation is enabled")
    minimum_stock_level: Optional[float] = Field(None, description="Minimum stock threshold")
    re_order_point: Optional[float] = Field(None, description="Reorder point threshold")
    re_order_quantity: Optional[float] = Field(None, description="Recommended reorder quantity")
    current_stock_level: Optional[float] = Field(None, description="Current stock level")
    quantity_reserved: Optional[float] = Field(None, description="Reserved stock quantity")
    quantity_available: Optional[float] = Field(None, description="Available stock quantity")
    total_stock_value: Optional[float] = Field(None, description="Total stock value")
    stock_status: Optional[str] = Field(None, description="Computed stock posture")
    total_locations: int = Field(0, description="Number of locations holding this inventory")
    expiring_soon_count: int = Field(0, description="Number of expiring lots")
    location_breakdown: List[InventoryLocationBreakdownPayload] = Field(
        default_factory=list,
        description="Per-location stock posture",
    )
    expiring_lots: List[InventoryLotSnapshotPayload] = Field(
        default_factory=list,
        description="Expiring lot snapshots",
    )


class InventoryItemResponsePayload(McpPayloadModel):
    id: str = Field(..., description="Inventory item identifier")
    name: str = Field(..., description="Inventory item display name")
    sku: str = Field("", description="Inventory item SKU snapshot")
    barcode: str = Field("", description="Inventory item barcode snapshot")
    description: str = Field("", description="Inventory item description")
    inventory_type: Optional[str] = Field(None, description="Inventory item type")
    inventory_category: Optional[str] = Field(None, description="Inventory category display name")
    track_stock: Optional[bool] = Field(None, description="Whether stock is tracked")
    track_lot: Optional[bool] = Field(None, description="Whether lot tracking is enabled")
    track_serial: Optional[bool] = Field(None, description="Whether serial tracking is enabled")
    track_expiry: Optional[bool] = Field(None, description="Whether expiry tracking is enabled")
    allow_negative_stock: Optional[bool] = Field(None, description="Whether negative stock is allowed")
    minimum_stock_level: Optional[float] = Field(None, description="Minimum stock threshold")
    reorder_point: Optional[float] = Field(None, description="Reorder point threshold")
    reorder_quantity: Optional[float] = Field(None, description="Recommended reorder quantity")
    status: Optional[str] = Field(None, description="Inventory item lifecycle or stock status")
    quantity: Optional[float] = Field(None, description="Current quantity")
    quantity_reserved: Optional[float] = Field(None, description="Reserved quantity")
    quantity_available: Optional[float] = Field(None, description="Available quantity")
    total_stock_value: Optional[float] = Field(None, description="Total stock value")
    avg_purchase_price: Optional[float] = Field(None, description="Average purchase price")
    purchase_price: Optional[float] = Field(None, description="Latest purchase price")
    location_name: str = Field("", description="Primary stock location name")
    location_count: int = Field(0, description="Number of locations holding the item")
    location_breakdown: List[InventoryLocationBreakdownPayload] = Field(
        default_factory=list,
        description="Per-location stock posture",
    )
    serial_count: int = Field(0, description="Number of serials")
    lot_count: int = Field(0, description="Number of lots")
    expiry_date: Optional[str] = Field(None, description="Nearest expiry date")
    days_to_expiry: Optional[int] = Field(None, description="Days until expiry")
    last_movement_at: Optional[str] = Field(None, description="ISO-8601 last movement timestamp")
    product_variant: Optional[str] = Field(None, description="Associated product variant reference")


class InventoryCategoryResponsePayload(McpPayloadModel):
    id: Optional[str] = Field(None, description="Inventory category identifier")
    name: Optional[str] = Field(None, description="Inventory category name")
    structural: Optional[bool] = Field(None, description="Whether the category is structural only")
    default_location_id: Optional[str] = Field(None, description="Default stock location identifier")
    parent_id: Optional[str] = Field(None, description="Parent category identifier")
    is_active: Optional[bool] = Field(None, description="Whether the category is active")
    description: Optional[str] = Field(None, description="Inventory category description")


class InventoryCategoryPagePayload(McpPayloadModel):
    count: Optional[int] = Field(None, description="Total matching category count")
    next: Optional[str] = Field(None, description="Next page URL")
    previous: Optional[str] = Field(None, description="Previous page URL")
    results: List[InventoryCategoryResponsePayload] = Field(default_factory=list, description="Category results")


class InventoryCollectionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    company_code: Optional[str] = Field(None, description="Workspace company code")
    query: Optional[str] = Field(None, description="Applied search query")
    count: int = Field(0, description="Number of returned records")
    limit: Optional[int] = Field(None, description="Applied result limit")
    results: List[InventoryResponsePayload] = Field(default_factory=list, description="Inventory results")


class InventoryDetailResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    company_code: Optional[str] = Field(None, description="Workspace company code")
    inventory: InventoryResponsePayload = Field(..., description="Inventory payload")


class InventoryItemCollectionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    company_code: Optional[str] = Field(None, description="Workspace company code")
    query: Optional[str] = Field(None, description="Applied search query")
    count: int = Field(0, description="Number of returned inventory items")
    limit: Optional[int] = Field(None, description="Applied result limit")
    results: List[InventoryItemResponsePayload] = Field(
        default_factory=list,
        description="Inventory item results",
    )


class InventoryCategoryCollectionResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    category: InventoryCategoryPagePayload | List[InventoryCategoryResponsePayload] | List[InventoryResponsePayload] = Field(
        ...,
        description="Category list/tree payload or category inventory results",
    )


class InventoryCategoryDetailResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    category: InventoryCategoryResponsePayload = Field(..., description="Inventory category payload")


class InventoryAlertsResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    expiring_days: int = Field(..., description="Expiry lookahead window in days")
    low_stock: List[InventoryResponsePayload] = Field(default_factory=list, description="Low-stock ledgers")
    needs_reorder: List[InventoryResponsePayload] = Field(default_factory=list, description="Reorder queues")
    out_of_stock: List[InventoryResponsePayload] = Field(default_factory=list, description="Out-of-stock ledgers")
    expiring_soon: List[InventoryResponsePayload] = Field(default_factory=list, description="Expiring ledgers")


class InventoryAnalyticsCategoryBreakdownPayload(McpPayloadModel):
    category_name: str = Field(..., validation_alias="category__name", description="Category name")
    count: int = Field(0, description="Inventories in the category")
    total_value: Optional[float] = Field(None, description="Total stock value for the category")


class InventoryAnalyticsStockStatusDistributionPayload(McpPayloadModel):
    in_stock: int = Field(0, description="In-stock inventory count")
    low_stock: int = Field(0, description="Low-stock inventory count")
    out_of_stock: int = Field(0, description="Out-of-stock inventory count")


class InventoryAnalyticsExpiringSoonPayload(McpPayloadModel):
    inventory_name: str = Field("", description="Inventory name")
    lot_number: str = Field("", description="Lot number")
    expiry_date: Optional[str] = Field(None, description="Expiry date")
    quantity: Optional[float] = Field(None, description="Expiring quantity")
    location_name: str = Field("", description="Location name")


class InventoryAnalyticsPayload(McpPayloadModel):
    total_inventories: int = Field(0, description="Total inventory count")
    active_inventories: int = Field(0, description="Active inventory count")
    low_stock_count: int = Field(0, description="Low-stock inventory count")
    out_of_stock_count: int = Field(0, description="Out-of-stock inventory count")
    total_stock_value: Optional[float] = Field(None, description="Aggregate stock value")
    category_breakdown: List[InventoryAnalyticsCategoryBreakdownPayload] = Field(
        default_factory=list,
        description="Category-level stock analytics",
    )
    stock_status_distribution: InventoryAnalyticsStockStatusDistributionPayload = Field(
        default_factory=InventoryAnalyticsStockStatusDistributionPayload,
        description="Stock status distribution",
    )
    top_value_items: List[InventoryResponsePayload] = Field(default_factory=list, description="Top-value inventory ledgers")
    expiring_soon: List[InventoryAnalyticsExpiringSoonPayload] = Field(
        default_factory=list,
        description="Expiring inventory lots",
    )


class InventoryAnalyticsResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    company_code: Optional[str] = Field(None, description="Workspace company code")
    analytics: InventoryAnalyticsPayload = Field(..., description="Workspace stock analytics payload")


class InventoryMutationResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    inventory: InventoryResponsePayload = Field(..., description="Inventory create/update result")


class InventoryCategoryMutationResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    category: InventoryCategoryResponsePayload = Field(..., description="Inventory category create/update result")


class InventoryActionPayload(McpPayloadModel):
    notes: Optional[str] = Field(None, description="Operator notes for the action")
    reason: Optional[str] = Field(None, description="Business reason for the action")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional action metadata")


class InventoryStockAdjustmentResultPayload(McpPayloadModel):
    message: str = Field("", description="Action result message")
    old_quantity: Optional[float] = Field(None, description="Quantity before adjustment")
    new_quantity: Optional[float] = Field(None, description="Quantity after adjustment")
    change: Optional[float] = Field(None, description="Applied quantity change")


class InventoryActionResultPayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    inventory: InventoryStockAdjustmentResultPayload = Field(..., description="Backend inventory action response")
