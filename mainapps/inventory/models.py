


from decimal import Decimal
import uuid
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.contrib.auth.models import Permission, Group
from django.core.exceptions import ValidationError
from django.conf import settings
from mptt.models import MPTTModel, TreeForeignKey
from django.utils.crypto import get_random_string
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from mainapps.content_type_linking_models.models import (
    ProfileMixin,
    TenantStampedUUIDModel,
    UUIDBaseModel,
    _sync_identity_fields,
)
from django.db import transaction
from django.db.models import F
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError


def _sync_profile_lookup_value(value):
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class Address(models.Model):

    
    country = models.CharField(
        max_length=255,
        verbose_name=_('Country'),
        help_text=_('Country of the address'),
        null=True,
        blank=True
    )
    region = models.CharField(
        max_length=255,
        verbose_name=_('Region/State'),
        help_text=_('Region or state within the country'),
        null=True,
        blank=True
    )
    subregion = models.CharField(
        max_length=255,
        verbose_name=_('Subregion/Province'),
        help_text=_('Subregion or province within the region'),
        null=True,
        blank=True
    )
    city = models.CharField(
        max_length=255,
        verbose_name=_('City'),
        help_text=_('City of the address'),
        null=True,
        blank=True
    )
    apt_number = models.PositiveIntegerField(
        verbose_name=_('Apartment number'),
        null=True,
        blank=True
    )
    street_number = models.PositiveIntegerField(
        verbose_name=_('Street number'),
        null=True,
        blank=True
    )
    street = models.CharField(max_length=255,blank=False,null=True)

    postal_code = models.CharField(
        max_length=10,
        verbose_name=_('Postal code'),
        help_text=_('Postal code'),
        blank=True,
        null=True,
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        verbose_name=_('Latitude'),
        help_text=_('Geographical latitude of the address'),
        null=True,
        blank=True
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        verbose_name=_('Longitude'),
        help_text=_('Geographical longitude of the address'),
        null=True,
        blank=True
    )


class RecallPolicies(models.TextChoices):
    REMOVE = "0", _("Remove from Stock")
    NOTIFY_CUSTOMERS = "1", _("Notify Customers")
    REPLACE_PRODUCT = "3", _("Replace Item")
    DESTROY = "4", _("Destroy Item")
    REPAIR = "5", _("Repair Item")

class ReorderStrategies(models.TextChoices):
    FIXED_QUANTITY = "FQ", _("Fixed Quantity")
    FIXED_INTERVAL = "FI", _("Fixed Interval")
    DYNAMIC = "DY", _("Demand-Based")

class ExpirePolicies(models.TextChoices):
    REMOVE = "0", _("Dispose of Stock")
    RETURN_MANUFACTURER = "1", _("Return to Manufacturer")

class NearExpiryActions(models.TextChoices):
    DISCOUNT = "DISCOUNT", _("Sell at Discount")
    DONATE = "DONATE", _("Donate to Charity")
    DESTROY = "DESTROY", _("Destroy Immediately")
    RETURN = "RETURN", _("Return to Supplier")

class ForecastMethods(models.TextChoices):
    SIMPLE_AVERAGE = "SA", _("Simple Average")
    MOVING_AVERAGE = "MA", _("Moving Average")
    EXP_SMOOTHING = "ES", _("Exponential Smoothing")


INVENTORY_TYPE_CHOICES = [
    ('raw_material', _('Raw Material')),
    ('finished_good', _('Finished Good')),
    ('work_in_progress', _('Work In Progress')),
    ('maintenance_spare_part', _('Maintenance Spare Part')),
    ('consumable', _('Consumable')),
    ('tooling', _('Tooling')),
    ('packaging', _('Packaging')),
]


class InventoryItemStatus(models.TextChoices):
    DRAFT = 'draft', _('Draft')
    ACTIVE = 'active', _('Active')
    ARCHIVED = 'archived', _('Archived')
    DISCONTINUED = 'discontinued', _('Discontinued')

class InventoryPolicy(ProfileMixin):
    """
    Central policy framework governing inventory operations.
    Defines rules for stock management, replenishment, and risk mitigation.
    """
    
    class Meta:
        abstract = True
    unit =models.CharField(
        max_length=23,
        null=True,
        blank=True,
    )
    unit_name =models.CharField(
        max_length=23,
        null=True,
        blank=True,
    )
    re_order_point = models.IntegerField(
        _("Reorder Point"),
        default=10,
        help_text=_("Inventory-item level triggering replenishment (units)")
    )
    
    re_order_quantity = models.IntegerField(
        _("Reorder Quantity"),
        default=200,
        help_text=_("Standard quantity for automated replenishment")
    )
    

        # Safety Stock Parameters
    safety_stock_level = models.IntegerField(
        _("Safety Stock"),
        default=0,
        help_text=_("Buffer stock for demand/supply fluctuations")
    )
    
    minimum_stock_level = models.IntegerField(
        _("Safety Stock"),
        default=0,
        help_text=_("Buffer stock for demand/supply fluctuations")
    )
    
    
    supplier_lead_time = models.IntegerField(
        _("Supplier Lead Time"),
        default=0,
        help_text=_("Average replenishment duration (days)")
    )
    
    internal_processing_time = models.IntegerField(
        _("Internal Processing Time"),
        default=1,
        help_text=_("Days needed for internal order processing")
    )

    # Replenishment Strategy
    reorder_strategy = models.CharField(
        _("Replenishment Strategy"),
        max_length=2,
        choices=ReorderStrategies.choices,
        default=ReorderStrategies.FIXED_QUANTITY,
        help_text=_("Methodology for inventory replenishment")
    )

    # Expiration Management
    expiration_threshold = models.IntegerField(
        _("Expiration Alert Window"),
        default=30,
        help_text=_("Days before expiry to trigger alerts")
    )
    
    # # Cost Parameters
    # holding_cost_per_unit = models.DecimalField(
    #     _("Holding Cost"),
    #     max_digits=10,
    #     decimal_places=2,
    #     default=0.0,
    #     help_text=_("Annual storage cost per unit")
    # )
    
    # ordering_cost = models.DecimalField(
    #     _("Ordering Cost"),
    #     max_digits=10,
    #     decimal_places=2,
    #     default=0.0,
    #     help_text=_("Fixed cost per replenishment order")
    # )
    
    # stockout_cost = models.DecimalField(
    #     _("Stockout Cost"),
    #     max_digits=10,
    #     decimal_places=2,
    #     default=0.0,
    #     help_text=_("Estimated cost per unit of stockout")
    # )

    # Expiration Handling Policies
    expiration_policy = models.CharField(
        _("Expiration Handling"),
        max_length=200,
        choices=ExpirePolicies.choices,
        default=ExpirePolicies.REMOVE,
        help_text=_("Procedure for expired inventory items")
    )

    recall_policy = models.CharField(
        _("Recall Procedure"),
        max_length=200,
        choices=RecallPolicies.choices,
        default=RecallPolicies.REMOVE,
        help_text=_("Protocol for product recall scenarios")
    )

    # Near-Expiry Actions
    near_expiry_policy = models.CharField(
        _("Near-Expiry Action"),
        max_length=20,
        choices=NearExpiryActions.choices,
        default=NearExpiryActions.DISCOUNT,
        help_text=_("Action plan for items nearing expiration")
    )

    # Demand Forecasting
    forecast_method = models.CharField(
        _("Forecast Method"),
        max_length=2,
        choices=ForecastMethods.choices,
        default=ForecastMethods.SIMPLE_AVERAGE,
        help_text=_("Algorithm for demand prediction")
    )

    # Supplier Management
    supplier_reliability_score = models.DecimalField(
        _("Supplier Score"),
        max_digits=5,
        decimal_places=2,
        default=100.0,
        help_text=_("Performance rating (0-100 scale)")
    )
    
    alert_threshold = models.IntegerField(
        _("Alert Threshold"),
        default=10,
        help_text=_("Percentage variance to trigger stock alerts")
    )

    # System Integration
    external_system_id = models.CharField(
        _("External ID"),
        max_length=200,
        blank=True,
        null=True,
        help_text=_("Identifier in external ERP/WMS systems")
    )
    auto_archive_days = models.PositiveIntegerField(
        _("Auto-Archive Period"),
        default=365,
        help_text=_("Days of inactivity before archiving the inventory item")
    )

    @property
    def calculated_safety_stock(self):
        """Calculate safety stock based on demand variability and lead time"""
        return max(self.safety_stock_level, 10)  

    @property
    def get_unit(self):
        return self.unit_name
    
class InventoryProperty(InventoryPolicy):
    class Meta:
        abstract=True

    assembly = models.BooleanField(
        default=False,
        verbose_name=_('Assembly'),
        help_text=_('Can this inventory item be built from other inventory items?'),
    )

    batch_tracking_enabled = models.BooleanField(
        _("Batch Tracking"),
        default=False,
        help_text=_("Enable batch/lot number tracking for items")
    )

    automate_reorder = models.BooleanField(
        _("Auto-Replenish"),
        default=False,
        help_text=_("Enable automatic purchase orders at reorder point")
    )
    component = models.BooleanField(
        default=False,
        verbose_name=_('Component'),
        help_text=_('Can this inventory item be used to build other inventory items?'),
    )

    trackable = models.BooleanField(
        default=True,
        verbose_name=_('Trackable'),
        help_text=_('Does this inventory item track unique units?'),
    )

    testable = models.BooleanField(
        default=False,
        verbose_name=_('Testable'),
        help_text=_('Can this inventory item have test results recorded against it?'),
    )

    purchaseable = models.BooleanField(
        default=True,
        verbose_name=_('Purchaseable'),
        help_text=_('Can this inventory item be purchased from external suppliers?'),
    )

    salable = models.BooleanField(
        default=True,
        verbose_name=_('Salable'),
        help_text=_('Can this inventory item be sold to customers?'),
    )

    active = models.BooleanField(
        default=True, verbose_name=_('Active'), help_text=_('Is this inventory item active?')
    )

    locked = models.BooleanField(
        default=False,
        verbose_name=_('Locked'),
        help_text=_('Locked inventory items cannot be edited'),
    )

    virtual = models.BooleanField(
        default=False,
        verbose_name=_('Virtual'),
        help_text=_('Is this a virtual inventory, such as a software product or license?'),
    )
    

class InventoryCategory(ProfileMixin, MPTTModel):
    structural = models.BooleanField(
        default=False,
        verbose_name=_('Structural'),
        help_text=_(
            'Inventory items may not be directly assigned to a structural category, '
            'but may be assigned to child categories.'
        ),
    )
    
    default_location = TreeForeignKey(
        'stock.StockLocation',
        related_name='default_categories',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name=_('Default Location'),
        help_text=_('Default location for parts in this category'),
    )
    
    name = models.CharField(
        max_length=200, 
        help_text='It must be unique within a tenant profile.', 
        verbose_name='Category name*'
    )
  
    slug = models.SlugField(max_length=230, editable=False)
    is_active = models.BooleanField(default=True)
    
    parent = TreeForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="children",
        null=True,
        blank=True,
        verbose_name='Parent category',
        help_text=_('Parent to which this category falls'),
    )
    
    description = models.TextField(blank=True, null=True)

    class MPTTMeta:
        order_insertion_by = ["name"]

    class Meta:
        ordering = ["name"]
        verbose_name_plural = _("categories")
        constraints=[
            models.UniqueConstraint(fields=['name','profile'],name='unique_name_profile')
        ]
    
    @classmethod
    def get_verbose_names(cls, p=None):
        if str(p) == '0':
            return "Inventory Category"
        return "Inventory Categories"
    
    @property
    def get_label(self):
        return 'inventorycategory'
    
    @classmethod
    def return_numbers(cls, profile):
        profile_value = _sync_profile_lookup_value(profile)
        if profile_value is None:
            return 0
        return cls.objects.filter(models.Q(profile_id=profile_value) | models.Q(profile=str(profile_value))).count()

    @property
    def inventory_count(self):
        """Count of active inventory items in this category."""
        return self.inventory_items.filter(status=InventoryItemStatus.ACTIVE).count()
    
    def get_all_descendants(self):
        return self.get_descendants(include_self=True)
    
    def save(self, *args, **kwargs):
        if not self.default_location and self.parent:
            self.default_location = self.parent.default_location
            
        self.slug = f"{get_random_string(6)}{slugify(self.name)}-{self.profile}-{get_random_string(5)}"
        super(InventoryCategory, self).save(*args, **kwargs)
    
    def __str__(self):
        return self.name
    
    @classmethod
    def tabular_display(cls):
        return [{"name": 'Name'}, {'is_active': 'Active'}]


class InventoryItem(TenantStampedUUIDModel):
    product_template_id = models.UUIDField(
        blank=True,
        null=True,
        db_index=True,
        verbose_name=_("Product Template ID"),
    )
    product_variant_id = models.UUIDField(
        blank=True,
        null=True,
        db_index=True,
        verbose_name=_("Product Variant ID"),
    )
    name_snapshot = models.CharField(max_length=255, verbose_name=_("Name Snapshot"))
    sku_snapshot = models.CharField(max_length=100, blank=True, default="", verbose_name=_("SKU Snapshot"))
    barcode_snapshot = models.CharField(max_length=100, blank=True, default="", verbose_name=_("Barcode Snapshot"))
    description = models.TextField(blank=True, default="", verbose_name=_("Description"))
    inventory_category = models.ForeignKey(
        InventoryCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_items',
        verbose_name=_("Inventory Category"),
    )
    inventory_type = models.CharField(
        max_length=50,
        choices=INVENTORY_TYPE_CHOICES,
        default='raw_material',
        verbose_name=_("Inventory Type"),
    )
    default_uom_code = models.CharField(max_length=32, blank=True, default="", verbose_name=_("Default UOM"))
    stock_uom_code = models.CharField(max_length=32, blank=True, default="", verbose_name=_("Stock UOM"))
    track_stock = models.BooleanField(default=True, verbose_name=_("Track Stock"))
    track_lot = models.BooleanField(default=False, verbose_name=_("Track Lot"))
    track_serial = models.BooleanField(default=False, verbose_name=_("Track Serial"))
    track_expiry = models.BooleanField(default=False, verbose_name=_("Track Expiry"))
    allow_negative_stock = models.BooleanField(default=False, verbose_name=_("Allow Negative Stock"))
    reorder_point = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    reorder_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    minimum_stock_level = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    safety_stock_level = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    default_supplier = models.ForeignKey(
        'company.Company',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='inventory_item_defaults',
        limit_choices_to={'is_supplier': True},
        verbose_name=_("Default Supplier"),
    )
    status = models.CharField(
        max_length=20,
        choices=InventoryItemStatus.choices,
        default=InventoryItemStatus.ACTIVE,
        db_index=True,
        verbose_name=_("Status"),
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['name_snapshot']
        indexes = [
            models.Index(fields=['profile_id', 'status']),
            models.Index(fields=['inventory_category', 'inventory_type']),
            models.Index(fields=['sku_snapshot']),
            models.Index(fields=['barcode_snapshot']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['profile_id', 'product_variant_id'],
                condition=models.Q(product_variant_id__isnull=False),
                name='unique_inventory_item_profile_variant',
            ),
        ]

    @property
    def display_name(self):
        return self.name_snapshot

    def __str__(self):
        return self.name_snapshot


registerable_models = [InventoryCategory, InventoryItem]
