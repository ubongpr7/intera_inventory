import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from mainapps.inventory.models import Inventory, InventoryItem
from mainapps.orders.models import PurchaseOrder, PurchaseOrderLineItem, SalesOrder, SalesOrderLineItem
from mainapps.stock.models import StockItem


class OrderLineItemLegacyBridgeTests(SimpleTestCase):
    def setUp(self):
        self.inventory = Inventory(
            id=uuid.uuid4(),
            name="Copper Wire",
            profile_id=1,
            profile="1",
            inventory_type="raw_material",
            re_order_point=10,
            re_order_quantity=200,
            minimum_stock_level=0,
            safety_stock_level=0,
            expiration_threshold=30,
        )
        self.bridge_inventory_item = InventoryItem(
            id=uuid.uuid4(),
            profile_id=1,
            name_snapshot=self.inventory.name,
            inventory_type=self.inventory.inventory_type,
        )
        self.stock_item = StockItem(
            id=uuid.uuid4(),
            inventory=self.inventory,
            name="Legacy Stock Row",
            quantity=Decimal("5"),
        )
        self.purchase_order = PurchaseOrder(id=uuid.uuid4(), profile_id=1, profile="1")
        self.sales_order = SalesOrder(id=uuid.uuid4(), profile_id=1, profile="1")

    def test_purchase_order_line_does_not_auto_link_legacy_bridge_inventory_item(self):
        line_item = PurchaseOrderLineItem(
            purchase_order=self.purchase_order,
            stock_item=self.stock_item,
            quantity=2,
            unit_price=Decimal("10.00"),
        )

        with patch("mainapps.orders.models.PurchaseOrderLineItem.generate_batch_number", return_value="BATCH-001"):
            with patch("mainapps.orders.models.PurchaseOrderLineItem.full_clean"):
                with patch("mainapps.orders.models.UUIDBaseModel.save", autospec=True):
                    line_item.save()

        self.assertIsNone(line_item.inventory_item_id)
        self.assertEqual(line_item.stock_item_id, self.stock_item.id)

    def test_sales_order_line_does_not_auto_link_legacy_bridge_inventory_item(self):
        line_item = SalesOrderLineItem(
            sales_order=self.sales_order,
            inventory=self.inventory,
            quantity=Decimal("3"),
            unit_price=Decimal("12.50"),
        )

        with patch("mainapps.orders.models.SalesOrderLineItem.full_clean"):
            with patch("mainapps.orders.models.UUIDBaseModel.save", autospec=True):
                line_item.save()

        self.assertIsNone(line_item.inventory_item_id)
        self.assertEqual(line_item.inventory_id, self.inventory.id)

    def test_explicit_inventory_item_is_preserved(self):
        purchase_line = PurchaseOrderLineItem(
            purchase_order=self.purchase_order,
            stock_item=self.stock_item,
            inventory_item=self.bridge_inventory_item,
            quantity=1,
            unit_price=Decimal("9.00"),
        )
        sales_line = SalesOrderLineItem(
            sales_order=self.sales_order,
            inventory=self.inventory,
            inventory_item=self.bridge_inventory_item,
            quantity=Decimal("1"),
            unit_price=Decimal("8.00"),
        )

        with patch("mainapps.orders.models.PurchaseOrderLineItem.generate_batch_number", return_value="BATCH-002"):
            with patch("mainapps.orders.models.PurchaseOrderLineItem.full_clean"):
                with patch("mainapps.orders.models.UUIDBaseModel.save", autospec=True):
                    purchase_line.save()

        with patch("mainapps.orders.models.SalesOrderLineItem.full_clean"):
            with patch("mainapps.orders.models.UUIDBaseModel.save", autospec=True):
                sales_line.save()

        self.assertEqual(purchase_line.inventory_item_id, self.bridge_inventory_item.id)
        self.assertEqual(sales_line.inventory_item_id, self.bridge_inventory_item.id)
