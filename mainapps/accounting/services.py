from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from mainapps.stock.models import StockBalance

from .models import InventoryValuationSnapshot, JournalEntry, JournalLine


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


class AccountingError(ValueError):
    pass


class AccountingService:
    @classmethod
    @transaction.atomic
    def post_sale(cls, *, shipment, actor_user_id=None):
        key = f'sales-shipment:{shipment.id}'
        existing = JournalEntry.objects.filter(profile_id=shipment.profile_id, idempotency_key=key).first()
        if existing:
            return existing
        revenue = sum((_decimal(line.revenue_amount) for line in shipment.lines.all()), Decimal('0'))
        cogs = sum((_decimal(line.cogs_amount) for line in shipment.lines.all()), Decimal('0'))
        entry = JournalEntry.objects.create(
            profile_id=shipment.profile_id,
            entry_type=JournalEntry.EntryType.SALE,
            reference_type='sales_order_shipment',
            reference_id=str(shipment.id),
            entry_date=shipment.shipment_date or timezone.now().date(),
            memo=f'Sale posted for {shipment.reference}',
            idempotency_key=key,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        lines = []
        if revenue:
            lines.extend([
                JournalLine(profile_id=shipment.profile_id, entry=entry, account_code='1100-AR', debit=revenue, memo='Customer receivable'),
                JournalLine(profile_id=shipment.profile_id, entry=entry, account_code='4100-SALES', credit=revenue, memo='Sales revenue'),
            ])
        if cogs:
            lines.extend([
                JournalLine(profile_id=shipment.profile_id, entry=entry, account_code='5100-COGS', debit=cogs, memo='Cost of goods sold'),
                JournalLine(profile_id=shipment.profile_id, entry=entry, account_code='1200-INVENTORY', credit=cogs, memo='Inventory relieved'),
            ])
        JournalLine.objects.bulk_create(lines)
        return entry

    @classmethod
    @transaction.atomic
    def post_customer_return(cls, *, customer_return, actor_user_id=None):
        key = f'customer-return:{customer_return.id}'
        existing = JournalEntry.objects.filter(profile_id=customer_return.profile_id, idempotency_key=key).first()
        if existing:
            return existing
        revenue = sum((_decimal(line.revenue_reversal) for line in customer_return.lines.all()), Decimal('0'))
        cogs = sum((_decimal(line.cogs_reversal) for line in customer_return.lines.all()), Decimal('0'))
        entry = JournalEntry.objects.create(
            profile_id=customer_return.profile_id,
            entry_type=JournalEntry.EntryType.CUSTOMER_RETURN,
            reference_type='customer_return',
            reference_id=str(customer_return.id),
            entry_date=customer_return.return_date,
            memo=f'Customer return posted for {customer_return.reference}',
            idempotency_key=key,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        lines = []
        if revenue:
            lines.extend([
                JournalLine(profile_id=customer_return.profile_id, entry=entry, account_code='4200-SALES-RETURNS', debit=revenue, memo='Sales return reversal'),
                JournalLine(profile_id=customer_return.profile_id, entry=entry, account_code='1100-AR', credit=revenue, memo='Customer receivable reversal'),
            ])
        if cogs:
            lines.extend([
                JournalLine(profile_id=customer_return.profile_id, entry=entry, account_code='1200-INVENTORY', debit=cogs, memo='Returned inventory restored'),
                JournalLine(profile_id=customer_return.profile_id, entry=entry, account_code='5100-COGS', credit=cogs, memo='COGS reversal'),
            ])
        JournalLine.objects.bulk_create(lines)
        return entry

    @classmethod
    @transaction.atomic
    def capture_valuation(cls, *, profile_id, snapshot_date=None, costing_method='actual', actor_user_id=None):
        snapshot_date = snapshot_date or date.today()
        balances = StockBalance.objects.filter(profile_id=profile_id).select_related('inventory_item', 'stock_location', 'stock_lot')
        snapshots = []
        for balance in balances:
            quantity = _decimal(balance.quantity_on_hand)
            unit_cost = _decimal(getattr(balance.stock_lot, 'unit_cost', 0))
            snapshot, _ = InventoryValuationSnapshot.objects.update_or_create(
                profile_id=profile_id,
                snapshot_date=snapshot_date,
                inventory_item=balance.inventory_item,
                stock_location=balance.stock_location,
                costing_method=costing_method,
                defaults={
                    'quantity_on_hand': quantity,
                    'unit_cost': unit_cost,
                    'total_value': quantity * unit_cost,
                    'created_by_user_id': actor_user_id,
                    'updated_by_user_id': actor_user_id,
                },
            )
            snapshots.append(snapshot)
        return snapshots
