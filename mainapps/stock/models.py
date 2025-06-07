from __future__ import annotations
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from mainapps.company.models import Company
from mainapps.inventory.models import InventoryMixin
from mainapps.orders.models import *
from django.core.validators import MinValueValidator
from django.db import models
from mptt.models import MPTTModel, TreeForeignKey

class StockStatus(models.TextChoices):
    OK = 'ok', _('OK')
    ATTENTION = 'attention_needed', _('Attention needed')
    DAMAGED = 'damaged', _('Damaged')
    DESTROYED = 'destroyed', _('Destroyed')
    REJECTED = 'rejected', _('Rejected')
    LOST = 'lost', _('Lost')
    QUARANTINED = 'quarantined', _('Quarantined')
    RETURNED = 'returned', _('Returned')

class TrackingType(models.IntegerChoices):
    RECEIVED = 10, _('Items received from supplier')
    PURCHASE_ORDER_RECEIPT = 11, _('Received against purchase order')
    RETURNED_FROM_CUSTOMER = 12, _('Items returned from customer')
    SHIPPED = 20, _('Items shipped to customer')
    SALES_ORDER_SHIPMENT = 21, _('Shipped against sales order')
    CONSUMED_IN_BUILD = 22, _('Used in manufacturing process')
    STOCK_ADJUSTMENT = 30, _('Manual quantity adjustment')
    LOCATION_CHANGE = 31, _('Moved between locations')
    SPLIT_FROM_PARENT = 32, _('Split from parent stock')
    MERGED_WITH_PARENT = 33, _('Merged with parent stock')
    QUARANTINED = 40, _('Placed in quarantine')
    QUALITY_CHECK = 41, _('Quality inspection performed')
    REJECTED = 42, _('Rejected during inspection')
    STOCKTAKE = 50, _('Manual stock count performed')
    AUTO_RESTOCK = 51, _('Automatic restock triggered')
    EXPIRY_WARNING = 52, _('Near expiry date notification')
    STATUS_CHANGE = 60, _('Stock status updated')
    DAMAGE_REPORTED = 61, _('Damage reported on item')
    OTHER = 0, _('Other Uncategorized tracking event')

class StockLocationType(models.Model):
    name = models.CharField(
        unique=True,
        blank=False, 
        max_length=100, 
        verbose_name=_('Name'), 
        help_text=_('Brief name for the stock location type (unique)'),
    )
    description = models.CharField(
        blank=True,
        max_length=250,
        verbose_name=_('Description'),
        help_text=_('Longer form description of the stock location type (optional)'),
    )

    class Meta:
        verbose_name = _('Stock Location Type')
        verbose_name_plural = _('Stock Location Types')
        ordering = ['id']

    def __str__(self):
        return self.name

class StockLocation(ProfileMixin, MPTTModel):
    code = models.CharField(
        max_length=100,
        unique=True,
        editable=False,
        null=True,
        blank=True,
        verbose_name=_('Location Code'),
        help_text=_('Unique location identifier (auto-generated)')
    ) 
    
    name = models.CharField(max_length=200, null=True, blank=False)
    
    official = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_('Manager ID'),
        help_text=_('ID of the manager for this stock location'),
    )

    structural = models.BooleanField(
        default=False,
        verbose_name=_('Structural'),
        help_text=_(
            'Stock items may not be directly located into a structural stock location, '
            'but may be located to child locations.'
        ),
    )
    
    parent = TreeForeignKey(
        'self',
        null=True,
        blank=True,
        related_name='children',
        on_delete=models.CASCADE,
        verbose_name=_('Super Location'),
        help_text=_('The location this falls under eg if this is a sub location in a bigger location like warehouse'),
    )

    external = models.BooleanField(
        default=False,
        verbose_name=_('External'),
        help_text=_('This is an external stock location'),
    )

    location_type = models.ForeignKey(
        StockLocationType,
        on_delete=models.SET_NULL,
        verbose_name=_('Location type'),
        related_name='stock_locations',
        null=True,
        blank=True,
        help_text=_('Stock location type of this location'),
    )

    description = models.TextField(
        blank=True,
        null=True,
        verbose_name=_('Description'),
        help_text=_('Longer form description of the stock location (optional)'),
    )
    
    def __str__(self):
        return f"{self.name}- {self.code} ({self.location_type.name})" if self.location_type else self.name
    
    def save(self, *args, **kwargs):
        """Auto-generate location code on first save"""
        if not self.pk and self.location_type and self.profile:
            base = self.location_type.name.upper().replace(' ', '_')
            profile_id = self.profile
            
            last_code = StockLocation.objects.filter(
                location_type=self.location_type,
                profile=self.profile,
                code__startswith=f"{base}_{profile_id}_"
            ).order_by('-code').values_list('code', flat=True).first()

            sequence = 1
            if last_code:
                try:
                    sequence = int(last_code.split('_')[-1]) + 1
                except (ValueError, IndexError):
                    pass

            self.code = f"{base}_{profile_id}_{sequence:03d}"

        super().save(*args, **kwargs)

class StockItem(MPTTModel, InventoryMixin):
    name = models.CharField(
        max_length=200,
        null=True,
        blank=False,
        verbose_name=_('Name'),
        help_text=_('Name of the stock item'),
    )
    
    parent = TreeForeignKey(
        'self',
        verbose_name=_('Parent Stock Item'),
        on_delete=models.DO_NOTHING,
        blank=True,
        null=True,
        related_name='children',
        help_text=_('Link to another StockItem from which this StockItem was created'),
    )
    
    location = TreeForeignKey(
        StockLocation,
        on_delete=models.DO_NOTHING,
        verbose_name=_('Stock Location'),
        related_name='stock_items',
        blank=True,
        null=True,
        help_text=_('Where this StockItem is located'),
    )

    packaging = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('Packaging'),
        help_text=_('Description of how the StockItem is packaged (e.g. "reel", "loose", "tape" etc)'),
    )
    
    belongs_to = models.ForeignKey(
        'self',
        verbose_name=_('Installed In'),
        on_delete=models.CASCADE,
        related_name='installed_parts',
        blank=True,
        null=True,
        help_text=_('Is this item installed in another item?'),
    )

    customer = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_('Customer ID'),
        verbose_name=_('Customer ID'),
    )

    serial = models.CharField(
        verbose_name=_('Serial Number'),
        max_length=100,
        blank=True,
        null=True,
        help_text=_('Unique serial number for this StockItem'),
    )
    
    sku = models.CharField(
        verbose_name=_('Stock keeping unit'),
        max_length=100,
        blank=True,
        null=True,
        help_text=_('Stock keeping unit for this stock item'),
    )
    
    serial_int = models.IntegerField(default=0)
    
    link = models.URLField(
        verbose_name=_('External Link'),
        blank=True,
        null=True,
        help_text=_('Optional URL to link to an external resource'),
    )

    batch = models.CharField(
        verbose_name=_('Batch Code'),
        max_length=100,
        blank=True,
        null=True,
        help_text=_('Batch code for this stock item'),
    )

    quantity = models.DecimalField(
        verbose_name=_('Stock Quantity'),
        max_digits=15,
        decimal_places=5,
        validators=[MinValueValidator(0)],  
        default=1,
    )

    purchase_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.SET_NULL,
        verbose_name=_('Source Purchase Order'),
        related_name='stock_items',
        blank=True,
        null=True,
        help_text=_('Link to a PurchaseOrder (if this stock item was created from a PurchaseOrder)'),
    )

    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.SET_NULL,
        verbose_name=_('Destination Sales Order'),
        related_name='stock_items',
        null=True,
        blank=True,
        help_text=_("Link item to a SalesOrder")
    )

    expiry_date = models.DateField(
        blank=True,
        null=True,
        verbose_name=_('Expiry Date'),
        help_text=_('Expiry date for stock item. Stock will be considered expired after this date'),
    )

    stocktake_date = models.DateField(blank=True, null=True)

    stocktaker = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_('User  ID that performed the most recent stocktake'),
    )

    review_needed = models.BooleanField(default=False)

    delete_on_deplete = models.BooleanField(
        default=False,
        verbose_name=_('Delete on deplete'),
        help_text=_('Delete this Stock Item when stock is depleted'),
    )

    status = models.CharField(
        default=StockStatus.OK,
        choices=StockStatus.choices,
        max_length=50,
        verbose_name=_('Status'),
        help_text=_('Status of this StockItem '),
    )

    purchase_price = models.DecimalField(
        max_digits=30,
        decimal_places=7,
        blank=True,
        null=True,
        verbose_name=_('Purchase Price'),
        help_text=_('Single unit purchase price at the time of purchase'),
    )
    
    override_sales_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Temporary price override for this stock batch"
    )    

    notes = models.TextField(
        blank=True,
        null=True,
        verbose_name=_('Notes'),
        help_text=_('Extra notes field'),
    )
    
    def __str__(self):
        """Return a string representation of the StockItem."""
        return f"{self.name} {self.serial or ''} - {self.quantity}"
    
    def save(self, *args, **kwargs):
        if not self.sku:
            company_id = self.inventory.profile.id
            inv_id = self.inventory.id
            inv_type = self.inventory.inventory_type.name[:4].upper()
            category_code = self.inventory.category.name[:5].upper()

            count = StockItem.objects.filter(inventory=self.inventory).count() + 1
            self.sku = f"C{company_id}-{inv_type}-{category_code}-{inv_id:05d}-{count:05d}"
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = _('Stock Item')
        verbose_name_plural = _('Stock Items')
        ordering = ['name', 'serial']
        indexes = [
            models.Index(fields=['location']),
            models.Index(fields=['batch', 'serial']),
        ]

class StockPricing(models.Model):
    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name='pricings')
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_flat = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    discount_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)  
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)  

    price_effective_from = models.DateTimeField(default=timezone.now)
    price_effective_to = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.stock_item.name} - ₦{self.selling_price}"

    def get_discount_amount(self):
        return (self.selling_price * self.discount_rate / 100) + self.discount_flat

    def get_tax_amount(self):
        price_after_discount = self.selling_price - self.get_discount_amount()
        return price_after_discount * self.tax_rate / 100

    def get_total_price(self):
        return self.selling_price - self.get_discount_amount() + self.get_tax_amount()

class StockItemTracking(InventoryMixin):
    tracking_type = models.IntegerField(default=TrackingType.OTHER, choices=TrackingType.choices)

    item = models.ForeignKey(
        StockItem, on_delete=models.CASCADE, related_name='tracking_info'
    )

    date = models.DateTimeField(auto_now_add=True, editable=False)

    notes = models.CharField(
        blank=True,
        null=True,
        max_length=512,
        verbose_name=_('Notes'),
        help_text=_('Entry notes'),
    )

    user = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_('User  ID associated with this tracking info'),
    )

    deltas = models.JSONField(null=True, blank=True)

    @classmethod
    def get_verbose_names(cls, p=None):
        if str(p) == '0':
            return "Stock Tracking "
        return "Stock Tracking"

    @property
    def get_label(self):
        return 'stockitemtracking'

    @classmethod
    def return_numbers(cls, profile):
        return cls.objects.filter(inventory__profile=profile).count()

    class Meta:
        indexes = [
            models.Index(fields=['date', 'item'])
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(tracking_type__in=TrackingType.values),
                name='valid_tracking_type'
            )
        ]
class StockMovement(InventoryMixin):
    """Comprehensive stock movement tracking"""
    
    MOVEMENT_TYPES = [
        ('IN', 'Stock In'),
        ('OUT', 'Stock Out'),
        ('TRANSFER', 'Location Transfer'),
        ('ADJUSTMENT', 'Inventory Adjustment'),
        ('RETURN', 'Return'),
        ('DAMAGE', 'Damage/Loss'),
    ]
    
    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE)
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity_before = models.DecimalField(max_digits=15, decimal_places=5)
    quantity_after = models.DecimalField(max_digits=15, decimal_places=5)
    quantity_changed = models.DecimalField(max_digits=15, decimal_places=5)
    
    from_location = models.ForeignKey(
        StockLocation, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='movements_from'
    )
    to_location = models.ForeignKey(
        StockLocation, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='movements_to'
    )
    
    reference_order = models.CharField(max_length=100, blank=True)
    user_id = models.CharField(max_length=255)  # From user microservice
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['stock_item', 'timestamp']),
            models.Index(fields=['movement_type', 'timestamp']),
        ]
class AuditMixin(models.Model):
    """Mixin for comprehensive audit trails"""
    
    created_by = models.CharField(max_length=255, blank=True)
    modified_by = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
    
    # Track field changes
    change_history = models.JSONField(default=list, blank=True)
    
    class Meta:
        abstract = True
    
    def save(self, *args, **kwargs):
        # Track changes before saving
        if self.pk:
            old_instance = self.__class__.objects.get(pk=self.pk)
            changes = {}
            for field in self._meta.fields:
                old_value = getattr(old_instance, field.name)
                new_value = getattr(self, field.name)
                if old_value != new_value:
                    changes[field.name] = {
                        'old': str(old_value),
                        'new': str(new_value),
                        'timestamp': timezone.now().isoformat()
                    }
            
            if changes:
                self.change_history.append(changes)
        
        super().save(*args, **kwargs)
        
registerable_models = [StockLocationType, StockLocation, StockItemTracking, StockItem]
