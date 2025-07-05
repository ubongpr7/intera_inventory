


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
from mainapps.content_type_linking_models.models import ProfileMixin, UUIDBaseModel
from django.db import transaction
from django.db.models import F
from django.core.validators import RegexValidator
from django.core.exceptions import ValidationError


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
        help_text=_("Inventory level triggering replenishment (units)")
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
        help_text=_("Days of inactivity before archiving inventory")
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
        help_text=_('Can this Inventory be built from other Inventory?'),
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
        help_text=_('Can this Inventory be used to build other Inventory?'),
    )

    trackable = models.BooleanField(
        default=True,
        verbose_name=_('Trackable'),
        help_text=_('Does this Inventory have tracking for unique items?'),
    )

    testable = models.BooleanField(
        default=False,
        verbose_name=_('Testable'),
        help_text=_('Can this Inventory have test results recorded against it?'),
    )

    purchaseable = models.BooleanField(
        default=True,
        verbose_name=_('Purchaseable'),
        help_text=_('Can this Inventory be purchased from external suppliers?'),
    )

    salable = models.BooleanField(
        default=True,
        verbose_name=_('Salable'),
        help_text=_('Can this Inventory be sold to customers?'),
    )

    active = models.BooleanField(
        default=True, verbose_name=_('Active'), help_text=_('Is this Inventory active?')
    )

    locked = models.BooleanField(
        default=False,
        verbose_name=_('Locked'),
        help_text=_('Locked Inventory cannot be edited'),
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
            'Inventory may not be directly assigned to a structural category, '
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
        unique=True, 
        help_text='It must be unique', 
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
        return cls.objects.filter(profile=profile).count()

    @property
    def inventory_count(self):
        """Count of active inventory items in this category"""
        return self.inventories.filter(active=True).count()
    
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
class InventoryQuerySet(models.QuerySet):
    def active(self):
        return self.filter(active=True)
    
    def low_stock(self):
        return self.filter(
            stock_items__quantity__lte=models.F('minimum_stock_level')
        ).distinct()
    
    def needs_reorder(self):
        return self.filter(
            stock_items__quantity__lte=models.F('re_order_point')
        ).distinct()
    
    def by_category(self, category):
        return self.filter(category=category)
    
    def expiring_soon(self, days=30):
        cutoff_date = timezone.now().date() + timezone.timedelta(days=days)
        return self.filter(
            stock_items__expiry_date__lte=cutoff_date,
            stock_items__expiry_date__isnull=False
        ).distinct()

class InventoryManager(models.Manager):
    def get_queryset(self):
        return InventoryQuerySet(self.model, using=self._db)
    
    def active(self):
        return self.get_queryset().active()
    
    def low_stock(self):
        return self.get_queryset().low_stock()
    
    def needs_reorder(self):
        return self.get_queryset().needs_reorder()

class Inventory(InventoryProperty):
    objects = InventoryManager()
    name = models.CharField(
        _("Inventory Name"),
        max_length=255,
        help_text=_("Unique identifier for this inventory system")
    )
    
    description = models.TextField(
        _("Description"),
        blank=True,
        null=True,
        help_text=_("Detailed operational context and usage notes")
    )
    
    default_supplier = models.ForeignKey(
        'company.Company',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        verbose_name=_('Default Supplier'),
        help_text=_('Default supplier For the Inventory'),
        related_name='default_inventories',
        limit_choices_to={'is_supplier': True},

    )
    
    inventory_type = models.CharField(
        max_length=50,
        choices=[
            ('raw_material', _('Raw Material')),
            ('finished_good', _('Finished Good')),
            ('work_in_progress', _('Work In Progress')),
            ('maintenance_spare_part', _('Maintenance Spare Part')),
            ('consumable', _('Consumable')),
            ('tooling', _('Tooling')),
            ('packaging', _('Packaging')),
        ],
        default='raw_material',
        verbose_name=_('Inventory Type'),
        help_text=_('Type of inventory item')
    )
    
    category = models.ForeignKey(
        InventoryCategory,
        verbose_name=_("Classification Category"),
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='inventories',
        help_text=_("Hierarchical grouping for inventory items")
    )
    
    sync_status = models.CharField(
        max_length=20,
        choices=[
            ('SYNCED', 'Synced'),
            ('PENDING', 'Pending Sync'),
            ('ERROR', 'Sync Error'),
        ],
        default='PENDING'
    )
    
    last_sync_timestamp = models.DateTimeField(null=True, blank=True)
    sync_error_message = models.TextField(blank=True, null=True)
    
    # External system references for integration
    external_references = models.JSONField(
        default=dict,
        help_text="References to this inventory in external systems"
    )
    officer_in_charge = models.CharField(
        max_length=400,
        blank=True,
        null=True,
        verbose_name=_('Officer in Charge ID'),
        help_text=_('ID of the officer responsible for this inventory'),
    )
    class Meta:
        verbose_name_plural = 'Inventories'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['profile', 'active']),
            models.Index(fields=['category', 'inventory_type']),
            models.Index(fields=['external_system_id']),
            models.Index(fields=['re_order_point', 'minimum_stock_level']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['external_system_id', 'profile'], 
                name='unique_inventory_external_system_id_profile'
            ),
            
        ]    
    def generate_external_id(self):
        """Atomically generate unique external ID in PROFILE_INITIALS-SEQ format"""
        with transaction.atomic():
            last_inventory= Inventory.objects.filter(profile=self.profile).order_by('-created_at').last()
            number_in_category= Inventory.objects.filter(category=self.category).count()+1
            if last_inventory:
                last_reference=last_inventory.external_system_id.split('-')[-1]

                last_reference=int(last_inventory.external_system_id.split('-')[-1])
            else:
                last_reference=0
            last_reference+=1

            initials = ''.join([word[0] for word in self.category.name.split() if word])[:3].upper()+'-'
            initials+= ''.join([word[0] for word in self.inventory_type.split('_') if word])[:3].upper()
            if len(initials) < 2:
                initials = self.category.name[:3].upper()
                
            
            return f"INV-{initials}-{self.profile}{number_in_category}-{last_reference:04d}"

    def save(self, *args, **kwargs):
        # if not self.external_system_id:
        self.external_system_id = self.generate_external_id()
        super().save(*args, **kwargs)
    
    def clean(self):
        if self.minimum_stock_level > self.re_order_point:
            raise ValidationError({'minimum_stock_level': f'Minimum stock level {self.minimum_stock_level} cannot be greater than Reorder point {self.re_order_point}'})
        if self.re_order_point > self.re_order_quantity:
            raise ValidationError({'re_order_point': f'Reorder stock level {self.re_order_point} cannot be greater than Reorder quantity {self.re_order_quantity}'})
        if self.safety_stock_level < 0:
            raise ValidationError("Safety stock cannot be negative")
        if self.expiration_threshold < 0:
            raise ValidationError("Expiration threshold must be positive")
        if self.supplier_lead_time < 0:
            raise ValidationError("Lead time cannot be negative")
    @property
    def total_stock_value(self):
        """Calculate total value of all stock for this inventory"""
        return self.stock_items.aggregate(
            total=models.Sum(
                models.F('quantity') * models.F('purchase_price'),
                output_field=models.DecimalField()
            )
        )['total'] or Decimal('0.00')
    
    @property
    def current_stock_level(self):
        """Get current total stock across all locations"""
        return self.stock_items.aggregate(
            total=models.Sum('quantity')
        )['total'] or 0
    
    



    @property
    def stock_status(self):
        """Determine stock status based on current levels"""
        current = self.current_stock_level
        if current <= 0:
            return 'OUT_OF_STOCK'
        elif current <= self.minimum_stock_level:
            return 'LOW_STOCK'
        elif current <= self.re_order_point:
            return 'REORDER_NEEDED'
        return 'IN_STOCK'
    
    @property
    def days_of_stock_remaining(self):
        """Calculate days of stock remaining based on average consumption"""
        # This would require historical consumption data
        # Implementation depends on your tracking requirements
        pass
    def __str__(self):
        return self.name

class InventoryBatch(UUIDBaseModel):
    """Batch/lot tracking for inventory items"""
    inventory = models.ForeignKey(
        Inventory,
        on_delete=models.CASCADE,
        related_name='batches'
    )
    batch_number = models.CharField(_("Batch/Lot Number"), max_length=100)
    manufacture_date = models.DateField(_("Manufacture Date"))
    expiry_date = models.DateField(_("Expiry Date"))
    quantity_received = models.DecimalField(
        _("Quantity Received"),
        max_digits=15,
        decimal_places=5
    )
    remaining_quantity = models.DecimalField(
        _("Remaining Quantity"),
        max_digits=15,
        decimal_places=5
    )
    location = models.ForeignKey(
        'stock.StockLocation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    
    class Meta:
        verbose_name_plural = _("Inventory Batches")
        unique_together = ('inventory', 'batch_number')
        
    @property
    def days_to_expiry(self):
        return (self.expiry_date - timezone.now().date()).days
        
    def __str__(self):
        return f"{self.batch_number} - {self.inventory.name}"
    

class InventoryMixinManager(models.Manager):
    """
    Custom manager for the Inventory model.
    Provides methods for querying inventories.
    """
    def for_inventory(self, inventory):
        """
        Get inventories associated with a specific inventory.

        Args:
            inventory (Inventory): The inventory to filter by.

        Returns:
            QuerySet: QuerySet of inventories associated with the specified inventory.
        """
        return self.get_queryset().filter(inventory=inventory)


class InventoryMixin(UUIDBaseModel):
    """
    Abstract model providing a common base for models associated with an inventory.

    Attributes:
        inventory (Inventory): The inventory to which the model belongs.
    """
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, null=True)
    
    objects = InventoryMixinManager()

    class Meta:
        abstract = True
    
    def save(self, *args, **kwargs):
        """
        Override the save method to perform additional actions when saving.

        Args:
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        super().save(*args, **kwargs)

class TransactionType(models.TextChoices):
    """Defines types of inventory transactions."""
    PO_RECEIVE = 'PO_RECEIVE', 'Purchase Order Receipt'
    PO_COMPLETE = 'PO_COMPLETE', 'Purchase Order Completion'
    ADJUSTMENT = 'ADJUSTMENT', 'Inventory Adjustment'
    SALE = 'SALE', 'Customer Sale'
    RETURN = 'RETURN', 'Inventory Return'
    LOSS = 'LOSS', 'Inventory Loss'


    
class InventoryTransaction(ProfileMixin):
    
    

    item = models.ForeignKey(
        'stock.StockItem',
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    quantity = models.IntegerField(
        help_text="Positive for additions, negative for deductions"
    )
    unit_price = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0.0,
        null=True,
        blank=True,
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
        default='PO_COMPLETE'
    )
    reference = models.CharField(
        max_length=64,
        help_text="Associated document number (PO, SO, etc)"
    )
    user =models.CharField(
        max_length=100,
        blank=True,
        null=True,
        help_text="User who performed the transaction"
    )
    
    notes = models.TextField(
        blank=True,
        help_text="Additional transaction details"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.transaction_type} - {self.item.name} ({self.quantity})"

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Inventory Transaction'
        verbose_name_plural = 'Inventory Transactions'

registerable_models = [Inventory, InventoryCategory]

