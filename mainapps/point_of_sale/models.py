from django.db import models, transaction
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models import F, ExpressionWrapper, DecimalField, Sum
from decimal import Decimal
import uuid
import json

class UUIDBaseModel(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("ID"),
        help_text=_("Unique identifier for the model instance.")
    )
    created_by = models.CharField(
        max_length=400, 
        null=True,
        blank=True,
        verbose_name=_('Created By'),
        help_text=_('User who created this model instance.')
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Created At'),
        help_text=_('Timestamp when this model instance was created.')
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Updated At'),
        help_text=_('Timestamp when this model instance was last updated.')
    )

    class Meta:
        abstract = True

class ProfileMixin(UUIDBaseModel):
    profile = models.CharField(
        max_length=400,
        null=False,
        blank=True,
        verbose_name=_('Profile'),
        help_text=_('Profile of the user or entity associated with this model.'),
        editable=False
    )
    created_by = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name=_('Created By'),
        help_text=_('User ID of the creator'),
        editable=False
    )

    class Meta:
        abstract = True

class SyncMixin(ProfileMixin):
    sync_identifier = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    is_synced = models.BooleanField(default=False, db_index=True)
    last_sync_attempt = models.DateTimeField(null=True, blank=True)
    sync_version = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True

class POSConfiguration(SyncMixin):
    """POS Configuration settings"""
    name = models.CharField(max_length=100, default="Default POS")
    currency = models.CharField(max_length=3, default="USD")
    tax_inclusive = models.BooleanField(default=False)
    default_tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.0000)
    allow_negative_stock = models.BooleanField(default=False)
    require_customer = models.BooleanField(default=False)
    auto_print_receipt = models.BooleanField(default=True)
    receipt_header = models.TextField(blank=True)
    receipt_footer = models.TextField(blank=True)
    allow_split_payment = models.BooleanField(default=True)
    max_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=100.00)
    
    def __str__(self):
        return f"POS Config - {self.name}"

class POSTerminal(SyncMixin):
    """POS Terminal"""
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    configuration = models.ForeignKey(POSConfiguration, on_delete=models.PROTECT, to_field='sync_identifier')

    def __str__(self):
        return f"{self.name} ({'Active' if self.is_active else 'Inactive'})"

class Customer(SyncMixin):
    """Customer model for POS"""
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    loyalty_points = models.PositiveIntegerField(default=0)
    
    def __str__(self):
        return self.name

class Table(SyncMixin):
    """Restaurant table model"""
    number = models.CharField(max_length=20)
    name = models.CharField(max_length=100, blank=True)
    capacity = models.PositiveIntegerField(default=4)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['profile', 'number']
    
    def __str__(self):
        return f"Table {self.number}"

class POSSession(SyncMixin):
    """POS Session"""
    STATUS_CHOICES = [
        ('open', _('Open')),
        ('closed', _('Closed')),
        ('suspended', _('Suspended'))
    ]
    
    terminal = models.ForeignKey(POSTerminal, on_delete=models.PROTECT, to_field='sync_identifier')
    user = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    opening_time = models.DateTimeField(auto_now_add=True)
    closing_time = models.DateTimeField(null=True, blank=True)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expected_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    @property
    def total_sales(self):
        return self.orders.filter(status='completed').aggregate(
            total=Sum('total_amount')
        )['total'] or Decimal('0.00')
    
    def close_session(self, closing_balance):
        self.status = 'closed'
        self.closing_time = timezone.now()
        self.closing_balance = closing_balance
        self.expected_balance = self.opening_balance + self.total_sales
        self.save()

class POSOrder(SyncMixin):
    """POS Order"""
    STATUS_CHOICES = [
        ('draft', _('Draft')),
        ('pending', _('Pending')),
        ('completed', _('Completed')),
        ('cancelled', _('Cancelled')),
        ('refunded', _('Refunded'))
    ]
    
    session = models.ForeignKey(POSSession, on_delete=models.PROTECT, related_name='orders', to_field='sync_identifier')
    customer = models.ForeignKey('Customer', on_delete=models.SET_NULL, null=True, blank=True, to_field='sync_identifier')
    table = models.ForeignKey('Table', on_delete=models.SET_NULL, null=True, blank=True, to_field='sync_identifier')
    order_number = models.CharField(max_length=50, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    
    # Amounts
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tip_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # Metadata
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self.generate_order_number()
        super().save(*args, **kwargs)
    
    def generate_order_number(self):
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        return f"POS-{timestamp}-{str(self.id)[:8]}"
    
    def calculate_totals(self):
        """Calculate order totals"""
        self.subtotal = sum(item.line_total for item in self.items.all())
        self.tax_amount = sum(item.tax_amount for item in self.items.all())
        self.total_amount = self.subtotal + self.tax_amount + self.tip_amount - self.discount_amount
        self.save()
    
    def complete_order(self):
        """Complete the order"""
        self.status = 'completed'
        self.completed_at = timezone.now()
        self.save()

class POSOrderItem(SyncMixin):
    """POS Order Item"""
    order = models.ForeignKey(POSOrder, on_delete=models.CASCADE, related_name='items', to_field='sync_identifier')
    product_variant_id = models.CharField(max_length=255)  # Reference to product service
    product_name = models.CharField(max_length=255)
    variant_name = models.CharField(max_length=255, blank=True)
    
    quantity = models.DecimalField(max_digits=10, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    
    # Calculated fields
    line_subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # Customizations
    customizations = models.JSONField(default=dict, blank=True)
    special_instructions = models.TextField(blank=True)
    
    def save(self, *args, **kwargs):
        self.calculate_amounts()
        super().save(*args, **kwargs)
    
    def calculate_amounts(self):
        """Calculate line amounts"""
        self.line_subtotal = self.quantity * self.unit_price
        
        # Add customization costs
        customization_cost = self.calculate_customization_cost()
        self.line_subtotal += customization_cost
        
        # Calculate discount
        self.discount_amount = self.line_subtotal * (self.discount_percent / 100)
        
        # Calculate tax
        taxable_amount = self.line_subtotal - self.discount_amount
        self.tax_amount = taxable_amount * self.tax_rate
        
        # Calculate total
        self.line_total = taxable_amount + self.tax_amount
    
    def calculate_customization_cost(self):
        """Calculate additional cost from customizations"""
        total_cost = Decimal('0.00')
        if self.customizations:
            for group_id, selections in self.customizations.items():
                if isinstance(selections, list):
                    for selection in selections:
                        if isinstance(selection, dict) and 'price' in selection:
                            total_cost += Decimal(str(selection['price']))
                elif isinstance(selections, dict) and 'price' in selections:
                    total_cost += Decimal(str(selections['price']))
        return total_cost * self.quantity

class POSPayment(SyncMixin):
    """POS Payment - supports split payments"""
    PAYMENT_METHODS = [
        ('cash', _('Cash')),
        ('card', _('Card')),
        ('mobile', _('Mobile Payment')),
        ('qr', _('QR Code')),
        ('loyalty', _('Loyalty Points')),
        ('gift_card', _('Gift Card'))
    ]
    
    order = models.ForeignKey(POSOrder, on_delete=models.CASCADE, related_name='payments', to_field='sync_identifier')
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    
    # Payment details
    reference_number = models.CharField(max_length=255, blank=True)
    cash_received = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    change_given = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # Status
    is_processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    def process_payment(self):
        """Process the payment"""
        self.is_processed = True
        self.processed_at = timezone.now()
        
        # Calculate change for cash payments
        if self.payment_method == 'cash' and self.cash_received:
            self.change_given = max(0, self.cash_received - self.amount)
        
        self.save()

class POSDiscount(SyncMixin):
    """POS Discount rules"""
    DISCOUNT_TYPES = [
        ('percentage', _('Percentage')),
        ('fixed', _('Fixed Amount'))
    ]
    
    name = models.CharField(max_length=100)
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPES)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=False)
    
    # Conditions
    min_order_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    
    def calculate_discount(self, order_amount):
        """Calculate discount amount for given order amount"""
        if self.min_order_amount and order_amount < self.min_order_amount:
            return Decimal('0.00')
        
        if self.discount_type == 'percentage':
            discount = order_amount * (self.value / 100)
        else:
            discount = self.value
        
        if self.max_discount_amount:
            discount = min(discount, self.max_discount_amount)
        
        return discount

class POSReceipt(SyncMixin):
    """POS Receipt"""
    order = models.OneToOneField(POSOrder, on_delete=models.CASCADE, to_field='sync_identifier')
    receipt_number = models.CharField(max_length=50, unique=True)
    printed_at = models.DateTimeField(null=True, blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)
    email_address = models.EmailField(blank=True)
    
    def generate_receipt_number(self):
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        return f"RCP-{timestamp}-{str(self.id)[:8]}"
    
    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = self.generate_receipt_number()
        super().save(*args, **kwargs)

class POSHoldOrder(SyncMixin):
    """Held orders that can be retrieved later"""
    order_data = models.JSONField()
    hold_reason = models.CharField(max_length=255, blank=True)
    held_by = models.CharField(max_length=255)
    retrieved_at = models.DateTimeField(null=True, blank=True)
    retrieved_by = models.CharField(max_length=255, blank=True)
    
    def retrieve_order(self, user_id):
        """Mark order as retrieved"""
        self.retrieved_at = timezone.now()
        self.retrieved_by = user_id
        self.save()

class ConflictLog(ProfileMixin):
    """
    Logs data conflicts during synchronization between local and remote databases.

    Fields:
        - model_name: The name of the model where the conflict occurred.
        - local_data: The local version of the data.
        - remote_data: The remote version of the data.
        - resolved_data: The final resolved version after conflict handling.
        - resolution: How the conflict was resolved (local, remote, merged).
        - created_at: When the conflict occurred.
        - resolved_at: When the conflict was resolved.
    """
    model_name = models.CharField(max_length=255)
    local_data = models.JSONField()
    remote_data = models.JSONField()
    resolved_data = models.JSONField(null=True, blank=True)
    resolution = models.CharField(
        max_length=20,
        choices=[
            ('local_wins', 'Local Version Kept'),
            ('remote_wins', 'Remote Version Applied'),
            ('merged', 'Data Merged')
        ]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Conflict Log - {self.model_name}"
