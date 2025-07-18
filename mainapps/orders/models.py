
import logging
import os
import sys
from datetime import datetime
from decimal import Decimal
import uuid
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.fields import GenericForeignKey,GenericRelation

from mainapps.content_type_linking_models.models import ProfileMixin, UUIDBaseModel
from mptt.models import TreeForeignKey

from django.utils import timezone
from mainapps.company.models import  Company, CompanyAddress, Contact 
from mainapps.inventory.models import InventoryMixin 
from subapps.utils.statuses import *
from decimal import Decimal, ROUND_HALF_UP

class PurchaseOrderLineItem(UUIDBaseModel):
    purchase_order = models.ForeignKey(
        'PurchaseOrder',
        on_delete=models.CASCADE,
        related_name='line_items'
    )
    stock_item = models.ForeignKey(
        'stock.StockItem',
        related_name='po_line_items',
        on_delete=models.CASCADE,
        null=True
    )
    quantity = models.PositiveIntegerField(
        validators=[MinValueValidator(1)]
    )
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)
    discount_rate = models.DecimalField(
        max_digits=5, decimal_places=2,
        default=0.0,
        help_text="Discount rate as a percentage (e.g. 5.0)"
    )
    tax_rate = models.DecimalField(
        max_digits=5, decimal_places=2,
        default=0.0
    )
    description = models.TextField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    manufactured_date = models.DateField(null=True, blank=True)
    batch_number = models.CharField(max_length=30, blank=True)
    fully_received = models.BooleanField(default=False)

    def generate_batch_number(self) -> str:
        while True:
            code = str(uuid.uuid4().int)[:12]
            if not PurchaseOrderLineItem.objects.filter(batch_number=code).exists():
                return code

    def save(self, *args, **kwargs):
        if not self.batch_number:
            self.batch_number = self.generate_batch_number()
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self):
        if self.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")
        if self.unit_price < 0:
            raise ValidationError("Unit price must be non-negative.")

    @property
    def tax_amount(self):
        return (self.unit_price * self.quantity) * (self.tax_rate / Decimal("100"))

    @property
    def discount_amount(self):
        return (self.unit_price * self.quantity) * (self.discount_rate / Decimal("100"))

    @property
    def total_price(self):
        subtotal = self.quantity * self.unit_price
        return (subtotal + self.tax_amount - self.discount_amount).quantize(
            Decimal('0.00'), rounding=ROUND_HALF_UP
        )

    def __str__(self):
        return f"{self.quantity} x {self.stock_item} @ {self.unit_price}"


class TotalPriceMixin(UUIDBaseModel):

    """Mixin which provides 'total_price' field for an order."""

    class Meta:
        """Meta for MetadataMixin."""

        abstract = True

    

    order_currency =models.CharField(
        max_length=10,
        blank=True,
        null=True,
        verbose_name=_('Order Currency'),
    )

class Order(ProfileMixin):
    """
    Abstract model for an order.

    Instances of this class:

    - PurchaseOrder
    - SalesOrder

    Attributes:
        - reference: Unique order number / reference / code
        - description: Long-form description (required)
        - notes: Extra note field (optional)
        - issue_date: Date the order was issued
        - complete_date: Date the order was completed
        - responsible: User (or group) responsible for managing the order
    """

    class Meta:
        """
        Metaclass options. Abstract ensures no database table is created.
        """
        abstract = True
    

    
    description = models.CharField(
        max_length=250,
        blank=True,
        verbose_name=_('Description'),
        help_text=_('Order description (optional)'),
    )
    notes = models.TextField(
        blank=True,
        verbose_name=_('Notes'),
        help_text=_('Additional notes (optional)'),
    )

    

    link = models.URLField(
        blank=True, verbose_name=_('Link'), help_text=_('Link to an external page')
    )

    delivery_date = models.DateField(
        blank=True,
        null=True,
        verbose_name=_('Delivery Date'),
        help_text=_(
            'Expected date for order delivery. Order will be overdue after this date.'
        ),
    )
    received_date=models.DateField(
        blank=True,
        null=True,
        verbose_name=_('Received Date'),
        help_text=_(
            'Date order was received'
        ),
    )
    complete_date = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name=_('Completion Date'),
        help_text=_('Date order was completed'),
    )
    
    

    responsible = models.CharField(
        max_length=400,
        blank=True,
        null=True,
        verbose_name=_('Responsible'),
        help_text=_('User or group responsible for this order'),
    )

    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        verbose_name=_('Contact Person'),
        help_text=_('Point of contact for this order, that is the person you should keep in contact with for this order in the affiliated business'),
        related_name='+',
    )

    address = models.ForeignKey(
        CompanyAddress,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        verbose_name=_('Address'),
        help_text=_('Company address for this order of the affiliated business'),
        related_name='+',
    )
    
    
    def generate_reference(self, prefix:str,instance:models.Model):
        """Atomically generate unique PO reference in PREFIX-YYYYMMDD-SEQ format"""
        with transaction.atomic():
            profile = self.profile
            order=instance.objects.filter(profile= self.profile).order_by('-created_at').first()

            sequence=0
            if order:
                sequence = int(order.reference.split('-')[-1])
            sequence +=1
            date_str = timezone.now().strftime("%Y%m%d")
            
            
            components = [
                prefix.upper(),
                profile,
                date_str, 
                f"{sequence:04d}"
            ]
            
            return '-'.join(components)


class PurchaseOrderStatus(models.TextChoices):
    """Defines a set of status codes for a PurchaseOrder."""
    PENDING = 'pending', _('Pending')
    ISSUED = 'issued', _('Isshued')
    COMPLETED = 'completed', _('Complete')
    CANCELLED = 'cancelled', _('Cancelled')
    OVERDUE = 'overdue', _('Overdue')
    RECEIVED= 'received', _('Received')
    REJECTED = 'rejected',_('Rejected')
    APPROVED='approved','Approved'
    LOST = 'lost', _('Lost')
    RETURNED = 'returned', _('Returned')


class PurchaseOrder(TotalPriceMixin, Order):
    """A PurchaseOrder represents goods shipped inwards from an external supplier.

    Attributes:
        supplier: Reference to the company supplying the goods in the order
        supplier_reference: Optional field for supplier order reference code
        received_by: User that received the goods
        target_date: Expected delivery target date for PurchaseOrder completion (optional)
    """

    reference = models.CharField(
        unique=True,
        max_length=64,
        verbose_name=_('Reference'),
        help_text=_('Order reference'),
        editable=False,
    )

    status = models.CharField(
        default=PurchaseOrderStatus.PENDING,
        choices=PurchaseOrderStatus.choices,
        help_text=_('Purchase order status'),
        max_length=20,
        verbose_name=_('Status'),
    )


    supplier = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        limit_choices_to={'is_supplier': True},
        related_name='+',
        verbose_name=_('Supplier'),
        help_text=_('Company from which the items are being ordered'),
    )

    supplier_reference = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name=_('Supplier Reference'),
        help_text=_('Supplier order reference code'),
    )

    received_by =models.CharField(
        max_length=400,
        blank=True,
        null=True,
        verbose_name=_('Received By'),
    )

    issue_date = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name=_('Issue Date'),
        help_text=_('Date order was issued'),
    )

    complete_date = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name=_('Completion Date'),
        help_text=_('Date order was completed'),
    )
    workflow_state = models.CharField(
        max_length=50,
        choices=[
            ('DRAFT', 'Draft'),
            ('PENDING_APPROVAL', 'Pending Approval'),
            ('APPROVED', 'Approved'),
            ('SENT_TO_SUPPLIER', 'Sent to Supplier'),
            ('PARTIALLY_RECEIVED', 'Partially Received'),
            ('FULLY_RECEIVED', 'Fully Received'),
            ('CLOSED', 'Closed'),
        ],
        default='DRAFT'
    )
    
    approval_required = models.BooleanField(default=False)
    approved_by = models.CharField(max_length=255, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Add budget tracking
    budget_code = models.CharField(max_length=50, blank=True)
    department = models.CharField(max_length=100, blank=True)
    
    def calculate_total(self):
        """Dynamic total from line items"""
        total = self.line_items.aggregate(
            total=models.Sum('total_price')
        )['total'] or Decimal('0.00')
        return total

    @property
    def total_price(self):
        return self.calculate_total()
    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self.generate_reference("PO",PurchaseOrder)
        super().save(*args, **kwargs)


class SalesOrder(TotalPriceMixin, Order):
    """A SalesOrder represents a list of goods shipped outwards to a customer."""

    
    customer_reference = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name=_('Customer Reference '),
        help_text=_('Customer order reference code'),
    )

    shipment_date = models.DateTimeField(
        blank=True, null=True, verbose_name=_('Shipment Date')
    )

    shipped_by = models.CharField(
        max_length=400,
        blank=True,
        null=True,
        verbose_name=_('Shipped By'),
        help_text=_('User or group responsible for shipping this order'),
    )



class SalesOrderShipment(InventoryMixin):
    """The SalesOrderShipment model represents a physical shipment made against a SalesOrder.

    - Points to a single SalesOrder object
    - Multiple SalesOrderAllocation objects point to a particular SalesOrderShipment
    - When a given SalesOrderShipment is "shipped", stock items are removed from stock

    Attributes:
        order: SalesOrder reference
        shipment_date: Date this shipment was "shipped" (or null)
        checked_by: User reference field indicating who checked this order
        reference: Custom reference text for this shipment (e.g. consignment number?)
        notes: Custom notes field for this shipment
    """

    class Meta:
        """Metaclass defines extra model options."""

        # Shipment reference must be unique for a given sales order
        unique_together = ['order', 'reference']
   
    order = models.ForeignKey(
        SalesOrder,
        on_delete=models.CASCADE,
        blank=False,
        null=False,
        related_name='+',
        verbose_name=_('Order'),
        help_text=_('Sales Order'),
    )

    shipment_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Shipment Date'),
        help_text=_('Date of shipment'),
    )

    delivery_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_('Delivery Date'),
        help_text=_('Date of delivery of shipment'),
    )

    checked_by = models.CharField(
        max_length=400,
        blank=True,
        null=True,
        verbose_name=_('Checked By'),
    )

    reference = models.CharField(
        max_length=100,
        blank=False,
        verbose_name=_('Shipment'),
        help_text=_('Shipment number'),
        default='1',
    )

    tracking_number = models.CharField(
        max_length=100,
        blank=True,
        unique=False,
        verbose_name=_('Tracking Number'),
        help_text=_('Shipment tracking information'),
    )

    invoice_number = models.CharField(
        max_length=100,
        blank=True,
        unique=False,
        verbose_name=_('Invoice Number'),
        help_text=_('Reference number for associated invoice'),
    )

    link = models.URLField(
        blank=True, verbose_name=_('Link'), help_text=_('Link to external page')
    )


class ReturnOrderStatus(models.TextChoices):
    PENDING = 'pending', _('Pending')
    AWAITING_PICKUP = 'awaiting_pickup', _('Awaiting Pickup')
    IN_TRANSIT = 'in_transit', _('In Transit')
    COMPLETED = 'completed', _('Completed')
    CANCELLED = 'cancelled', _('Cancelled')


class ReturnOrder(TotalPriceMixin, Order):
    """A ReturnOrder represents goods returned from a customer, e.g. an RMA or warranty.

    Attributes:
        customer: Reference to the customer
        sales_order: Reference to an existing SalesOrder (optional)
        status: The status of the order (refer to statuses.ReturnOrderStatus)
        attachment: (Attachment) attached files
    """
    reference = models.CharField(
        unique=True,
        max_length=64,
        blank=False,
        verbose_name=_('Reference'),
        help_text=_('Return Order reference'),
        # default=order.validators.generate_next_return_order_reference,
        # validators=[order.validators.validate_return_order_reference],
    )

    customer = models.ForeignKey(
        'company.Company',
        on_delete=models.SET_NULL,
        null=True,
        limit_choices_to={'is_customer': True},
        related_name='+',
        verbose_name=_('Customer'),
        help_text=_('Company from which items are being returned'),
    )


    status = models.CharField(
        default=ReturnOrderStatus.PENDING,
        choices=ReturnOrderStatus.choices,
        verbose_name=_('Status'),
        help_text=_('Return order status'),
    )

    customer_reference = models.CharField(
        max_length=64,
        blank=True,
        verbose_name=_('Customer Reference '),
        help_text=_('Customer order reference code'),
    )

    issue_date = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name=_('Issue Date'),
        help_text=_('Date order was issued'),
    )
    return_reason = models.TextField(
        blank=True,
        null=True,
        verbose_name=_('Return Reason'),
        help_text=_('Detailed reason for the return')
    )
    
    def save(self, ):
        if not self.reference:
            self.reference = self.generate_reference("RO",ReturnOrder)
        return super().save()
    
   
class ReturnOrderLineItem(UUIDBaseModel):
    return_order = models.ForeignKey(
        ReturnOrder,
        on_delete=models.CASCADE,
        related_name='line_items'
    )
    original_line_item = models.ForeignKey(
        PurchaseOrderLineItem,
        on_delete=models.PROTECT,
        related_name='returns'
    )
    quantity_returned = models.PositiveIntegerField()
    return_reason = models.TextField(blank=True)
    
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.0)
    discount = models.DecimalField(max_digits=15, decimal_places=2, default=0.0)
    
    @property
    def total_price(self):
        return (self.unit_price * self.quantity_returned) - self.discount
        
    def clean(self):
        if self.quantity_returned > self.original_line_item.quantity:
            raise ValidationError("Return quantity exceeds original order quantity")

registerable_models=[
    ReturnOrder,
    PurchaseOrder,
    SalesOrderShipment,
    SalesOrder,
    ReturnOrderLineItem,
    PurchaseOrderLineItem,

    ]

