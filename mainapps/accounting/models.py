from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from mainapps.content_type_linking_models.models import TenantStampedUUIDModel


class JournalEntry(TenantStampedUUIDModel):
    class EntryType(models.TextChoices):
        SALE = 'sale', 'Sale'
        CUSTOMER_RETURN = 'customer_return', 'Customer return'
        INVENTORY_ADJUSTMENT = 'inventory_adjustment', 'Inventory adjustment'
        PERIOD_CLOSE = 'period_close', 'Period close'

    entry_type = models.CharField(max_length=32, choices=EntryType.choices)
    reference_type = models.CharField(max_length=64)
    reference_id = models.CharField(max_length=128)
    entry_date = models.DateField()
    currency_code = models.CharField(max_length=10, blank=True, default='')
    memo = models.TextField(blank=True, default='')
    idempotency_key = models.CharField(max_length=200)

    class Meta:
        ordering = ['-entry_date', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['profile_id', 'idempotency_key'], name='unique_journal_entry_idempotency'),
        ]
        indexes = [
            models.Index(fields=['profile_id', 'entry_date', 'entry_type']),
            models.Index(fields=['profile_id', 'reference_type', 'reference_id']),
        ]


class JournalLine(TenantStampedUUIDModel):
    entry = models.ForeignKey(
        JournalEntry, 
        on_delete=models.CASCADE, 
        related_name='lines'
        )
    account_code = models.CharField(max_length=64)
    debit = models.DecimalField(
    max_digits=18, 
    decimal_places=5, 
    default=Decimal('0'), 
    validators=[MinValueValidator(Decimal('0'))]
    )
    credit = models.DecimalField(
    max_digits=18, 
    decimal_places=5, 
    default=Decimal('0'), 
    validators=[MinValueValidator(Decimal('0'))]
    )
    memo = models.TextField(blank=True, default='')

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(debit=0) | models.Q(credit=0), name='journal_line_one_side_only'),
            models.CheckConstraint(check=~(models.Q(debit=0) & models.Q(credit=0)), name='journal_line_nonzero'),
        ]


class InventoryValuationSnapshot(TenantStampedUUIDModel):
    snapshot_date = models.DateField()
    inventory_item = models.ForeignKey('inventory.InventoryItem', on_delete=models.PROTECT, related_name='valuation_snapshots')
    stock_location = models.ForeignKey('stock.StockLocation', on_delete=models.PROTECT, related_name='valuation_snapshots')
    costing_method = models.CharField(max_length=32, default='actual')
    quantity_on_hand = models.DecimalField(max_digits=18, decimal_places=5, default=0)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=5, default=0)
    total_value = models.DecimalField(max_digits=18, decimal_places=5, default=0)
    currency_code = models.CharField(max_length=10, blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['profile_id', 'snapshot_date', 'inventory_item', 'stock_location', 'costing_method'], name='unique_inventory_valuation_snapshot'),
        ]
        indexes = [models.Index(fields=['profile_id', 'snapshot_date', 'inventory_item'])]

