from decimal import Decimal

from django.test import TestCase

from mainapps.inventory.models import InventoryItem
from mainapps.orders.models import (
    CustomerReturn,
    CustomerReturnLine,
    SalesOrder,
    SalesOrderLineItem,
    SalesOrderShipment,
    SalesOrderShipmentLine,
)
from mainapps.stock.models import StockLocation
from subapps.services.stock_domain import StockDomainService

from .models import InventoryValuationSnapshot, JournalEntry
from .services import AccountingService


class AccountingWorkflowTests(TestCase):
    def setUp(self):
        self.profile_id = 931
        self.location = StockLocation.objects.create(profile_id=self.profile_id, name='Main store')
        self.item = InventoryItem.objects.create(
            profile_id=self.profile_id,
            name_snapshot='Coffee',
            inventory_type='finished_good',
            stock_uom_code='unit',
            default_uom_code='unit',
            track_stock=True,
            track_lot=True,
            salable=True,
        )
        receipt = StockDomainService.receive_stock(
            inventory_item=self.item,
            stock_location=self.location,
            quantity='10',
            quantity_uom_code='unit',
            unit_cost='4',
            lot_number='COFFEE-1',
            force_lot=True,
            reference_type='test_receipt',
            reference_id='coffee-1',
        )
        self.order = SalesOrder.objects.create(profile_id=self.profile_id, description='Coffee sale')
        self.order_line = SalesOrderLineItem.objects.create(
            sales_order=self.order,
            inventory_item=self.item,
            quantity='2',
            quantity_uom_code='unit',
            unit_price='10',
        )
        self.shipment = SalesOrderShipment.objects.create(order=self.order, shipment_date='2026-09-27')
        self.shipment_line = SalesOrderShipmentLine.objects.create(
            shipment=self.shipment,
            sales_order_line=self.order_line,
            stock_location=self.location,
            stock_lot=receipt['stock_lot'],
            quantity_shipped='2',
            unit_cost='4',
            cogs_amount='8',
            revenue_amount='20',
            gross_margin='12',
        )

    def test_sale_journal_is_balanced_and_idempotent(self):
        entry = AccountingService.post_sale(shipment=self.shipment)
        self.assertEqual(entry.lines.count(), 4)
        self.assertEqual(
            sum(line.debit for line in entry.lines.all()),
            sum(line.credit for line in entry.lines.all()),
        )
        repeated = AccountingService.post_sale(shipment=self.shipment)
        self.assertEqual(repeated.id, entry.id)
        self.assertEqual(JournalEntry.objects.count(), 1)

    def test_customer_return_journal_reverses_revenue_and_cogs(self):
        customer_return = CustomerReturn.objects.create(profile_id=self.profile_id, sales_order=self.order)
        line = CustomerReturnLine.objects.create(
            customer_return=customer_return,
            shipment_line=self.shipment_line,
            quantity_returned='1',
            unit_cost='4',
            cogs_reversal='4',
            revenue_reversal='10',
        )
        entry = AccountingService.post_customer_return(customer_return=customer_return)
        self.assertEqual(entry.lines.count(), 4)
        self.assertEqual(sum(item.debit for item in entry.lines.all()), Decimal('14'))
        self.assertEqual(sum(item.credit for item in entry.lines.all()), Decimal('14'))
        self.assertEqual(AccountingService.post_customer_return(customer_return=customer_return).id, entry.id)

    def test_valuation_snapshot_is_repeat_safe(self):
        snapshots = AccountingService.capture_valuation(profile_id=self.profile_id, snapshot_date='2026-09-27')
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].total_value, Decimal('40'))
        repeated = AccountingService.capture_valuation(profile_id=self.profile_id, snapshot_date='2026-09-27')
        self.assertEqual(len(repeated), 1)
        self.assertEqual(InventoryValuationSnapshot.objects.count(), 1)
