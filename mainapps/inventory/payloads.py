from decimal import Decimal
from enum import Enum
import uuid
from datetime import date, datetime
from typing import Optional, Literal, Dict, Any, List

from pydantic import BaseModel, ConfigDict, Field

# ------------------------------------------------------------------------------
# Reusable enumerations (mirror Django choices)
# ------------------------------------------------------------------------------

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
    id: Optional[str] = Field(None, description="Stock lot identifier")
    lot_number: Optional[str] = Field(None, description="Lot number")
    expiry_date: Optional[str] = Field(None, description="ISO-8601 expiry date")
    remaining_quantity: Optional[float] = Field(None, description="Remaining quantity in the lot")
    status: Optional[str] = Field(None, description="Lot lifecycle status")


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
    category: InventoryCategoryPagePayload | List[InventoryCategoryResponsePayload] | List[InventoryItemResponsePayload] = Field(
        ...,
        description="Category list/tree payload or category inventory-item results",
    )


class InventoryCategoryDetailResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    category: InventoryCategoryResponsePayload = Field(..., description="Inventory category payload")


class InventoryAlertsResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    expiring_days: int = Field(..., description="Expiry lookahead window in days")
    low_stock: List[InventoryItemResponsePayload] = Field(default_factory=list, description="Low-stock inventory items")
    needs_reorder: List[InventoryItemResponsePayload] = Field(default_factory=list, description="Reorder queues")
    out_of_stock: List[InventoryItemResponsePayload] = Field(default_factory=list, description="Out-of-stock inventory items")
    expiring_soon: List[InventoryItemResponsePayload] = Field(default_factory=list, description="Expiring inventory items")


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
    top_value_items: List[InventoryItemResponsePayload] = Field(default_factory=list, description="Top-value inventory items")
    expiring_soon: List[InventoryAnalyticsExpiringSoonPayload] = Field(
        default_factory=list,
        description="Expiring inventory lots",
    )


class InventoryAnalyticsResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    company_code: Optional[str] = Field(None, description="Workspace company code")
    analytics: InventoryAnalyticsPayload = Field(..., description="Workspace stock analytics payload")


class InventoryItemSummaryResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    company_code: Optional[str] = Field(None, description="Workspace company code")
    inventory_item: InventoryItemResponsePayload = Field(..., description="Inventory item payload")


class InventoryItemMutationResponsePayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    inventory_item: InventoryItemResponsePayload = Field(..., description="Inventory-item create/update result")


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


class InventoryItemActionResultPayload(McpPayloadModel):
    profile_id: int = Field(..., description="Workspace profile identifier")
    inventory_item: InventoryStockAdjustmentResultPayload = Field(
        ...,
        description="Backend inventory-item action response",
    )
