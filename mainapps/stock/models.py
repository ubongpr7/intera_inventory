from __future__ import annotations
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from mainapps.company.models import Company
from django.core.validators import MinValueValidator
from django.db import models
from mptt.models import MPTTModel, TreeForeignKey
from mainapps.content_type_linking_models.models import ProfileMixin, TenantStampedUUIDModel, _sync_identity_fields

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


class StockLotStatus(models.TextChoices):
    OPEN = 'open', _('Open')
    QUARANTINED = 'quarantined', _('Quarantined')
    DEPLETED = 'depleted', _('Depleted')
    CLOSED = 'closed', _('Closed')


class StockSerialStatus(models.TextChoices):
    AVAILABLE = 'available', _('Available')
    RESERVED = 'reserved', _('Reserved')
    ISSUED = 'issued', _('Issued')
    DAMAGED = 'damaged', _('Damaged')
    RETURNED = 'returned', _('Returned')


class StockMovementType(models.TextChoices):
    RECEIPT = 'receipt', _('Receipt')
    ISSUE = 'issue', _('Issue')
    TRANSFER = 'transfer', _('Transfer')
    ADJUSTMENT = 'adjustment', _('Adjustment')
    RESERVATION = 'reservation', _('Reservation')
    RELEASE = 'release', _('Release')
    RETURN_IN = 'return_in', _('Return In')
    RETURN_OUT = 'return_out', _('Return Out')


class StockReservationStatus(models.TextChoices):
    ACTIVE = 'active', _('Active')
    PARTIALLY_FULFILLED = 'partially_fulfilled', _('Partially Fulfilled')
    FULFILLED = 'fulfilled', _('Fulfilled')
    RELEASED = 'released', _('Released')
    EXPIRED = 'expired', _('Expired')

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
    official_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)

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
    physical_address= models.CharField(max_length=255, null=True,blank=True)
    def __str__(self):
        return f"{self.name}- {self.code} ({self.location_type.name})" if self.location_type else self.name
    
    def save(self, *args, **kwargs):
        """Auto-generate location code on first save"""
        _sync_identity_fields(self, canonical_field='official_user_id', legacy_field='official')
        if self.parent and not self.physical_address:
            self.physical_address =self.parent.physical_address
            
        if  self.location_type and (self.profile_id is not None or self.profile) and not self.code:
            
            base = self.location_type.name.upper().replace(' ', '_')
            profile_id = self.profile_id if self.profile_id is not None else self.profile
            
            last_code = StockLocation.objects.filter(
                models.Q(profile_id=profile_id) | models.Q(profile=str(profile_id)),
                location_type=self.location_type,
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

class StockLot(TenantStampedUUIDModel):
    inventory_item = models.ForeignKey(
        'inventory.InventoryItem',
        on_delete=models.PROTECT,
        related_name='stock_lots',
    )
    supplier = models.ForeignKey(
        'company.Company',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_lots',
        limit_choices_to={'is_supplier': True},
    )
    purchase_order_line = models.ForeignKey(
        'orders.PurchaseOrderLineItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_lots',
    )
    goods_receipt_line = models.ForeignKey(
        'orders.GoodsReceiptLine',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_lots',
    )
    lot_number = models.CharField(max_length=100, blank=True)
    manufactured_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    currency_code = models.CharField(max_length=10, blank=True, default='')
    received_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    remaining_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    status = models.CharField(
        max_length=20,
        choices=StockLotStatus.choices,
        default=StockLotStatus.OPEN,
        db_index=True,
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['profile_id', 'inventory_item']),
            models.Index(fields=['profile_id', 'lot_number']),
            models.Index(fields=['expiry_date']),
        ]

    def save(self, *args, **kwargs):
        if not self.lot_number and self.goods_receipt_line_id:
            self.lot_number = f"LOT-{self.goods_receipt_line_id}"
        if self.remaining_quantity > 0 and self.status == StockLotStatus.DEPLETED:
            self.status = StockLotStatus.OPEN
        elif self.remaining_quantity <= 0 and self.status == StockLotStatus.OPEN:
            self.status = StockLotStatus.DEPLETED
        super().save(*args, **kwargs)


class StockSerial(TenantStampedUUIDModel):
    inventory_item = models.ForeignKey(
        'inventory.InventoryItem',
        on_delete=models.PROTECT,
        related_name='stock_serials',
    )
    stock_lot = models.ForeignKey(
        StockLot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='serials',
    )
    stock_location = models.ForeignKey(
        StockLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_serials',
    )
    serial_number = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20,
        choices=StockSerialStatus.choices,
        default=StockSerialStatus.AVAILABLE,
        db_index=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=['profile_id', 'inventory_item']),
            models.Index(fields=['profile_id', 'serial_number']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['profile_id', 'serial_number'],
                name='unique_stock_serial_profile_serial_number',
            )
        ]


class StockBalance(TenantStampedUUIDModel):
    inventory_item = models.ForeignKey(
        'inventory.InventoryItem',
        on_delete=models.CASCADE,
        related_name='stock_balances',
    )
    stock_location = models.ForeignKey(
        StockLocation,
        on_delete=models.CASCADE,
        related_name='stock_balances',
    )
    stock_lot = models.ForeignKey(
        StockLot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_balances',
    )
    quantity_on_hand = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    quantity_reserved = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    quantity_available = models.DecimalField(max_digits=15, decimal_places=5, default=0)

    class Meta:
        indexes = [
            models.Index(fields=['profile_id', 'inventory_item', 'stock_location']),
            models.Index(fields=['profile_id', 'quantity_available']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['inventory_item', 'stock_location', 'stock_lot'],
                name='unique_stock_balance_item_location_lot',
            )
        ]

    def save(self, *args, **kwargs):
        self.quantity_available = self.quantity_on_hand - self.quantity_reserved
        super().save(*args, **kwargs)


class StockReservation(TenantStampedUUIDModel):
    inventory_item = models.ForeignKey(
        'inventory.InventoryItem',
        on_delete=models.PROTECT,
        related_name='stock_reservations',
    )
    stock_lot = models.ForeignKey(
        StockLot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_reservations',
    )
    stock_serial = models.ForeignKey(
        StockSerial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_reservations',
    )
    stock_location = models.ForeignKey(
        StockLocation,
        on_delete=models.PROTECT,
        related_name='stock_reservations',
    )
    external_order_type = models.CharField(max_length=50)
    external_order_id = models.CharField(max_length=100)
    external_order_line_id = models.CharField(max_length=100, blank=True)
    reserved_quantity = models.DecimalField(max_digits=15, decimal_places=5)
    fulfilled_quantity = models.DecimalField(max_digits=15, decimal_places=5, default=0)
    status = models.CharField(
        max_length=30,
        choices=StockReservationStatus.choices,
        default=StockReservationStatus.ACTIVE,
        db_index=True,
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['profile_id', 'external_order_type', 'external_order_id']),
            models.Index(fields=['profile_id', 'status']),
        ]

    @property
    def remaining_quantity(self):
        return max(self.reserved_quantity - self.fulfilled_quantity, 0)

class StockMovement(TenantStampedUUIDModel):
    inventory_item = models.ForeignKey(
        'inventory.InventoryItem',
        on_delete=models.PROTECT,
        related_name='stock_movements',
    )
    stock_lot = models.ForeignKey(
        StockLot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements',
    )
    stock_serial = models.ForeignKey(
        StockSerial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements',
    )
    from_location = models.ForeignKey(
        StockLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movements_from',
    )
    to_location = models.ForeignKey(
        StockLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movements_to',
    )
    movement_type = models.CharField(
        max_length=20,
        choices=StockMovementType.choices,
        db_index=True,
    )
    quantity = models.DecimalField(max_digits=15, decimal_places=5)
    unit_cost = models.DecimalField(
        max_digits=15,
        decimal_places=5,
        blank=True,
        null=True,
    )
    reference_type = models.CharField(max_length=64, blank=True, default='')
    reference_id = models.CharField(max_length=100, blank=True, default='')
    actor_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-occurred_at', '-created_at']
        indexes = [
            models.Index(fields=['profile_id', 'inventory_item', 'occurred_at']),
            models.Index(fields=['profile_id', 'movement_type', 'occurred_at']),
            models.Index(fields=['profile_id', 'reference_type', 'reference_id']),
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
        
class StockAdjustment(models.Model):
    inventory_item = models.ForeignKey('inventory.InventoryItem', related_name='adjustments', on_delete=models.CASCADE)
    adjustment_type = models.CharField(max_length=50, choices=[('add', 'Add'), ('remove', 'Remove'), ('transfer', 'Transfer')])
    quantity_change = models.IntegerField()
    reason = models.TextField(blank=True, null=True)
    adjusted_by = models.CharField(max_length=255, blank=True, null=True)
    adjusted_by_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    adjusted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Adjustment of {self.quantity_change} for {self.inventory_item}"

    def save(self, *args, **kwargs):
        _sync_identity_fields(self, canonical_field='adjusted_by_user_id', legacy_field='adjusted_by')
        super().save(*args, **kwargs)

registerable_models = [
    StockLocationType,
    StockLocation,
    StockLot,
    StockSerial,
    StockBalance,
    StockReservation,
    StockMovement,
    StockAdjustment,
]
