from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from mainapps.content_type_linking_models.models import TenantStampedUUIDModel


class RecipeStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    ACTIVE = "active", _("Active")
    RETIRED = "retired", _("Retired")


class ProductionOrderStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    RELEASED = "released", _("Released")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class CostingMethod(models.TextChoices):
    ACTUAL = "actual", _("Actual")
    FIFO = "fifo", _("FIFO")
    STANDARD = "standard", _("Standard")
    WEIGHTED_AVERAGE = "weighted_average", _("Weighted average")


class Recipe(TenantStampedUUIDModel):
    name = models.CharField(max_length=255)
    finished_good = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.PROTECT,
        related_name="recipes",
    )
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=20, 
        choices=RecipeStatus.choices, 
        default=RecipeStatus.DRAFT
    )
    output_quantity = models.DecimalField(
        max_digits=15, 
        decimal_places=5, 
        validators=[MinValueValidator(Decimal("0.00001"))]
    )
    output_uom_code = models.CharField(
        max_length=32, 
        blank=True, 
        default=""
    )
    target_yield_percent = models.DecimalField(
        max_digits=7, 
        decimal_places=3, 
        default=Decimal("100"), 
        validators=[MinValueValidator(Decimal("0.001"))]
    )
    effective_from = models.DateTimeField(null=True, blank=True)
    effective_until = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default=""
    )
    costing_method = models.CharField(
        max_length=24, 
        choices=CostingMethod.choices, 
        default=CostingMethod.ACTUAL)
    standard_unit_cost = models.DecimalField(
        max_digits=15, 
        decimal_places=5, 
        default=Decimal("0")
    )

    class Meta:
        ordering = ["finished_good_id", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["profile_id", "finished_good", "version"], name="unique_recipe_profile_output_version"),
        ]
        indexes = [
            models.Index(fields=["profile_id", "finished_good", "status"]),
        ]

    def __str__(self):
        return f"{self.name} v{self.version}"


class RecipeLine(TenantStampedUUIDModel):
    class LineType(models.TextChoices):
        MATERIAL = "material", _("Material")
        PACKAGING = "packaging", _("Packaging")
        CONSUMABLE = "consumable", _("Consumable")
        BY_PRODUCT = "by_product", _("By-product")

    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="lines")
    component = models.ForeignKey(
        "inventory.InventoryItem",
        on_delete=models.PROTECT,
        related_name="recipe_lines",
    )
    quantity = models.DecimalField(
        max_digits=15, 
        decimal_places=5, 
        validators=[MinValueValidator(Decimal("0.00001"))]
    )
    uom_code = models.CharField(
        max_length=32, 
        blank=True, 
        default=""
    )
    scrap_percent = models.DecimalField(
        max_digits=7, 
        decimal_places=3, 
        default=Decimal("0"), 
        validators=[MinValueValidator(Decimal("0"))]
    )
    line_type = models.CharField(
        max_length=20, 
        choices=LineType.choices, 
        default=LineType.MATERIAL
    )
    is_optional = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["recipe", "component", "line_type"], name="unique_recipe_component_line_type"),
        ]


class RecipeOutput(TenantStampedUUIDModel):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="outputs")
    output_item = models.ForeignKey(
        "inventory.InventoryItem", 
        on_delete=models.PROTECT, 
        related_name="recipe_outputs"
    )
    quantity = models.DecimalField(
        max_digits=15, 
        decimal_places=5, 
        validators=[MinValueValidator(Decimal("0.00001"))]
    )
    uom_code = models.CharField(
        max_length=32, 
        blank=True, 
        default=""
    )
    allocation_percent = models.DecimalField(
        max_digits=7, 
        decimal_places=3, 
        default=Decimal("100"), 
        validators=[MinValueValidator(Decimal("0.001"))]
    )
    is_primary = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["recipe", "output_item"], name="unique_recipe_output_item"),
        ]
        indexes = [models.Index(fields=["profile_id", "output_item"])]


class WorkCenter(TenantStampedUUIDModel):
    code = models.CharField(max_length=80)
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    hourly_labor_rate = models.DecimalField(
        max_digits=15, 
        decimal_places=5, 
        default=Decimal("0")
    )
    hourly_overhead_rate = models.DecimalField(
        max_digits=15, 
        decimal_places=5, 
        default=Decimal("0")
    )
    capacity_minutes_per_day = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["profile_id", "code"], name="unique_work_center_profile_code")]


class WorkCenterBlock(TenantStampedUUIDModel):
    work_center = models.ForeignKey(WorkCenter, on_delete=models.CASCADE, related_name="blocks")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=255)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["profile_id", "work_center", "starts_at"])]


class RecipeOperation(TenantStampedUUIDModel):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="operations")
    sequence = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    work_center = models.ForeignKey(WorkCenter, on_delete=models.PROTECT, related_name="recipe_operations")
    expected_minutes = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["sequence"]
        constraints = [models.UniqueConstraint(fields=["recipe", "sequence"], name="unique_recipe_operation_sequence")]


class ProductionOrder(TenantStampedUUIDModel):
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT, related_name="production_orders")
    finished_good = models.ForeignKey("inventory.InventoryItem", on_delete=models.PROTECT, related_name="production_orders")
    stock_location = models.ForeignKey("stock.StockLocation", on_delete=models.PROTECT, related_name="production_orders")
    order_number = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=ProductionOrderStatus.choices, default=ProductionOrderStatus.DRAFT, db_index=True)
    planned_quantity = models.DecimalField(max_digits=15, decimal_places=5, validators=[MinValueValidator(Decimal("0.00001"))])
    actual_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    scrap_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=200, blank=True, default="")
    costing_period = models.ForeignKey(
        "CostingPeriod",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="production_orders",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["profile_id", "order_number"], name="unique_production_order_profile_number"),
            models.UniqueConstraint(
                fields=["profile_id", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_production_order_profile_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["profile_id", "status"]),
            models.Index(fields=["profile_id", "finished_good", "created_at"]),
        ]

    def __str__(self):
        return self.order_number


class ProductionOperationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    BLOCKED = "blocked", _("Blocked")
    CANCELLED = "cancelled", _("Cancelled")


class ProductionOperation(TenantStampedUUIDModel):
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="operations")
    recipe_operation = models.ForeignKey(RecipeOperation, on_delete=models.PROTECT, related_name="production_operations")
    sequence = models.PositiveIntegerField()
    name = models.CharField(max_length=255)
    work_center = models.ForeignKey(WorkCenter, on_delete=models.PROTECT, related_name="production_operations")
    status = models.CharField(max_length=20, choices=ProductionOperationStatus.choices, default=ProductionOperationStatus.PENDING)
    planned_minutes = models.PositiveIntegerField(default=0)
    actual_minutes = models.PositiveIntegerField(default=0)
    completed_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    scrap_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["sequence"]
        constraints = [models.UniqueConstraint(fields=["production_order", "sequence"], name="unique_production_operation_sequence")]


class QualityInspectionStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PASSED = "passed", _("Passed")
    FAILED = "failed", _("Failed")
    RELEASED = "released", _("Released")
    QUARANTINED = "quarantined", _("Quarantined")


class QualityInspection(TenantStampedUUIDModel):
    class Disposition(models.TextChoices):
        RELEASE = "release", _("Release")
        REWORK = "rework", _("Rework")
        SCRAP = "scrap", _("Scrap")
        RETURN_TO_SUPPLIER = "return_to_supplier", _("Return to supplier")
        HOLD = "hold", _("Hold")

    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="quality_inspections")
    output = models.ForeignKey("ProductionOutput", on_delete=models.CASCADE, null=True, blank=True, related_name="quality_inspections")
    status = models.CharField(max_length=20, choices=QualityInspectionStatus.choices, default=QualityInspectionStatus.PENDING)
    inspector_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    inspected_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    defect_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    released_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    quarantined_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    rework_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    disposition = models.CharField(max_length=32, choices=Disposition.choices, blank=True, default="")
    nonconformance_code = models.CharField(max_length=80, blank=True, default="")
    measurements = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")
    inspected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["profile_id", "status"])]


class NonconformanceStatus(models.TextChoices):
    OPEN = "open", _("Open")
    CONTAINED = "contained", _("Contained")
    INVESTIGATING = "investigating", _("Investigating")
    CORRECTIVE_ACTION = "corrective_action", _("Corrective action")
    VERIFIED = "verified", _("Verified")
    CLOSED = "closed", _("Closed")
    REJECTED = "rejected", _("Rejected")


class Nonconformance(TenantStampedUUIDModel):
    number = models.CharField(max_length=80)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="nonconformances")
    inspection = models.ForeignKey(QualityInspection, on_delete=models.SET_NULL, null=True, blank=True, related_name="nonconformances")
    status = models.CharField(max_length=24, choices=NonconformanceStatus.choices, default=NonconformanceStatus.OPEN, db_index=True)
    severity = models.CharField(max_length=20, default="medium")
    category = models.CharField(max_length=80, blank=True, default="")
    description = models.TextField(blank=True, default="")
    root_cause = models.TextField(blank=True, default="")
    containment_action = models.TextField(blank=True, default="")
    corrective_action = models.TextField(blank=True, default="")
    owner_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    due_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by_user_id = models.BigIntegerField(null=True, blank=True)
    closure_evidence = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["profile_id", "number"], name="unique_nonconformance_profile_number")]
        indexes = [models.Index(fields=["profile_id", "status", "severity"])]


class MaterialReservation(TenantStampedUUIDModel):
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="material_reservations")
    recipe_line = models.ForeignKey(RecipeLine, on_delete=models.PROTECT, related_name="material_reservations")
    stock_reservation = models.OneToOneField("stock.StockReservation", on_delete=models.PROTECT, related_name="material_reservation")
    quantity = models.DecimalField(max_digits=15, decimal_places=5)
    quantity_uom_code = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["profile_id", "production_order"])]


class MaterialRequirement(TenantStampedUUIDModel):
    class Status(models.TextChoices):
        PLANNED = "planned", _("Planned")
        RESERVED = "reserved", _("Reserved")
        SHORT = "short", _("Short")
        PARTIALLY_ISSUED = "partially_issued", _("Partially issued")
        ISSUED = "issued", _("Issued")
        FULFILLED = "fulfilled", _("Fulfilled")

    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="material_requirements")
    recipe_line = models.OneToOneField(RecipeLine, on_delete=models.PROTECT, related_name="material_requirement")
    component = models.ForeignKey("inventory.InventoryItem", on_delete=models.PROTECT, related_name="material_requirements")
    required_quantity = models.DecimalField(max_digits=15, decimal_places=5)
    reserved_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    issued_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    quantity_uom_code = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PLANNED, db_index=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["profile_id", "production_order", "status"]),
            models.Index(fields=["profile_id", "component", "status"]),
        ]

    @property
    def remaining_quantity(self):
        return max(self.required_quantity - self.issued_quantity, Decimal("0"))


class WasteRecord(TenantStampedUUIDModel):
    class WasteType(models.TextChoices):
        SCRAP = "scrap", _("Scrap")
        SPOILAGE = "spoilage", _("Spoilage")
        PROCESS_LOSS = "process_loss", _("Process loss")
        DAMAGED = "damaged", _("Damaged")
        EXPIRED = "expired", _("Expired")
        DISPOSAL = "disposal", _("Disposal")
        RECOVERED = "recovered", _("Recovered")

    class Disposition(models.TextChoices):
        DISCARDED = "discarded", _("Discarded")
        QUARANTINED = "quarantined", _("Quarantined")
        REWORKED = "reworked", _("Reworked")
        RECOVERED = "recovered", _("Recovered")

    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="waste_records")
    production_operation = models.ForeignKey(
        ProductionOperation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waste_records",
    )
    inventory_item = models.ForeignKey("inventory.InventoryItem", on_delete=models.PROTECT, related_name="waste_records")
    source_stock_lot = models.ForeignKey("stock.StockLot", on_delete=models.PROTECT, null=True, blank=True, related_name="waste_records")
    stock_movement = models.OneToOneField("stock.StockMovement", on_delete=models.PROTECT, null=True, blank=True, related_name="waste_record")
    recovered_stock_lot = models.ForeignKey("stock.StockLot", on_delete=models.PROTECT, null=True, blank=True, related_name="recovered_waste_records")
    quantity = models.DecimalField(max_digits=15, decimal_places=5)
    quantity_uom_code = models.CharField(max_length=32, blank=True, default="")
    unit_cost = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    disposal_cost = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    recovery_value = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    waste_type = models.CharField(max_length=24, choices=WasteType.choices, default=WasteType.SCRAP)
    disposition = models.CharField(max_length=24, choices=Disposition.choices, default=Disposition.DISCARDED)
    reason_code = models.CharField(max_length=80, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    recorded_at = models.DateTimeField(auto_now_add=True)
    idempotency_key = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["profile_id", "production_order", "recorded_at"]),
            models.Index(fields=["profile_id", "waste_type", "disposition"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["profile_id", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_waste_record_profile_idempotency",
            ),
        ]

    @property
    def net_cost(self):
        return (self.quantity * (self.unit_cost or Decimal("0"))) + self.disposal_cost - self.recovery_value


class CostingPeriod(TenantStampedUUIDModel):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        CLOSED = "closed", _("Closed")

    name = models.CharField(max_length=120)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by_user_id = models.BigIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-period_start"]
        constraints = [
            models.UniqueConstraint(fields=["profile_id", "period_start", "period_end"], name="unique_costing_period_profile_dates"),
        ]
        indexes = [models.Index(fields=["profile_id", "status", "period_start"])]


class MaterialIssue(TenantStampedUUIDModel):
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="material_issues")
    recipe_line = models.ForeignKey(RecipeLine, on_delete=models.PROTECT, related_name="material_issues")
    component = models.ForeignKey("inventory.InventoryItem", on_delete=models.PROTECT, related_name="production_material_issues")
    stock_lot = models.ForeignKey("stock.StockLot", on_delete=models.PROTECT, null=True, blank=True, related_name="material_issues")
    stock_movement = models.OneToOneField("stock.StockMovement", on_delete=models.PROTECT, related_name="material_issue")
    quantity = models.DecimalField(max_digits=15, decimal_places=5)
    quantity_uom_code = models.CharField(max_length=32, blank=True, default="")
    unit_cost = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    issued_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["profile_id", "component", "issued_at"]),
            models.Index(fields=["profile_id", "stock_lot"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["profile_id", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_material_issue_profile_idempotency",
            ),
        ]

    @property
    def total_cost(self):
        return self.quantity * (self.unit_cost or Decimal("0"))


class ProductionOutput(TenantStampedUUIDModel):
    class OutputKind(models.TextChoices):
        PRIMARY = "primary", _("Primary")
        CO_PRODUCT = "co_product", _("Co-product")
        BY_PRODUCT = "by_product", _("By-product")
        RECOVERED = "recovered", _("Recovered")
        WASTE = "waste", _("Waste")

    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="outputs")
    output_item = models.ForeignKey("inventory.InventoryItem", on_delete=models.PROTECT, related_name="production_outputs")
    stock_lot = models.OneToOneField("stock.StockLot", on_delete=models.PROTECT, related_name="production_output")
    quantity = models.DecimalField(max_digits=15, decimal_places=5)
    quantity_uom_code = models.CharField(max_length=32, blank=True, default="")
    unit_cost = models.DecimalField(max_digits=15, decimal_places=5, default=Decimal("0"))
    output_kind = models.CharField(max_length=20, choices=OutputKind.choices, default=OutputKind.PRIMARY)
    cost_allocation_percent = models.DecimalField(max_digits=7, decimal_places=3, default=Decimal("100"))
    produced_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["profile_id", "produced_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["profile_id", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_production_output_profile_idempotency",
            ),
        ]

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost


class ProductionCostEntry(TenantStampedUUIDModel):
    class CostType(models.TextChoices):
        LABOR = "labor", _("Labor")
        OVERHEAD = "overhead", _("Overhead")
        SUBCONTRACTING = "subcontracting", _("Subcontracting")
        OTHER = "other", _("Other")

    production_order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name="cost_entries")
    cost_type = models.CharField(max_length=30, choices=CostType.choices)
    amount = models.DecimalField(max_digits=15, decimal_places=5, validators=[MinValueValidator(Decimal("0"))])
    currency_code = models.CharField(max_length=10, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["profile_id", "production_order", "cost_type"]),
        ]
