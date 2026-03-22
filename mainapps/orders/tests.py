import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from mainapps.inventory.models import InventoryItem
from mainapps.orders.models import PurchaseOrder, PurchaseOrderLineItem, SalesOrder, SalesOrderLineItem


class OrderLineItemInventoryItemTests(SimpleTestCase):
    def setUp(self):
        self.inventory_item = InventoryItem(
            id=uuid.uuid4(),
            profile_id=1,
            name_snapshot="Copper Wire",
            inventory_type="raw_material",
        )
        self.purchase_order = PurchaseOrder(id=uuid.uuid4(), profile_id=1, profile="1")
        self.sales_order = SalesOrder(id=uuid.uuid4(), profile_id=1, profile="1")

    def test_purchase_order_line_requires_inventory_item(self):
        line_item = PurchaseOrderLineItem(
            purchase_order=self.purchase_order,
            quantity=2,
            unit_price=Decimal("10.00"),
        )

        with self.assertRaises(ValidationError):
            line_item.clean()

    def test_sales_order_line_string_uses_inventory_item_snapshot(self):
        line_item = SalesOrderLineItem(
            sales_order=self.sales_order,
            inventory_item=self.inventory_item,
            quantity=Decimal("3"),
            unit_price=Decimal("12.50"),
        )

        self.assertEqual(str(line_item), "3 x Copper Wire @ 12.50")

    def test_explicit_inventory_item_is_preserved_on_purchase_line(self):
        line_item = PurchaseOrderLineItem(
            purchase_order=self.purchase_order,
            inventory_item=self.inventory_item,
            quantity=1,
            unit_price=Decimal("9.00"),
        )

        line_item.clean()

        self.assertEqual(line_item.inventory_item_id, self.inventory_item.id)
