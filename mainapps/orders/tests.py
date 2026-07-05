import uuid
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core import mail
from django.test import SimpleTestCase, override_settings

from mainapps.inventory.models import InventoryItem
from mainapps.orders.models import PurchaseOrder, PurchaseOrderLineItem, SalesOrder, SalesOrderLineItem
from mainapps.orders.serializers import (
    GoodsReceiptLineSerializer,
    GoodsReceiptListSerializer,
    SalesOrderShipmentLineSerializer,
    SalesOrderShipmentListSerializer,
)
from subapps.services.emails.email_services import EmailService


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


class _FakeAncestorChain:
    def __init__(self, locations):
        self._locations = list(locations)

    def filter(self, **kwargs):
        if kwargs.get("structural") is True:
            return _FakeAncestorChain([location for location in self._locations if getattr(location, "structural", False)])
        return self

    def last(self):
        return self._locations[-1] if self._locations else None


class _FakeLocation:
    def __init__(self, name, *, structural=False, parent=None, location_id=None):
        self.id = location_id or uuid.uuid4()
        self.pk = self.id
        self.name = name
        self.structural = structural
        self.parent = parent

    def get_ancestors(self, include_self=False):
        locations = []
        current = self if include_self else self.parent
        while current is not None:
            locations.insert(0, current)
            current = getattr(current, "parent", None)
        return _FakeAncestorChain(locations)


class _FakeLinesManager:
    def __init__(self, lines):
        self._lines = list(lines)

    def select_related(self, *_args, **_kwargs):
        return self

    def __getitem__(self, item):
        return self._lines[item]

    def __iter__(self):
        return iter(self._lines)


class OrderStructuralLocationSerializerTests(SimpleTestCase):
    def setUp(self):
        self.inventory_item = InventoryItem(
            id=uuid.uuid4(),
            profile_id=1,
            name_snapshot="Copper Wire",
            inventory_type="raw_material",
        )
        self.purchase_order = PurchaseOrder(id=uuid.uuid4(), profile_id=1, profile="1")
        self.sales_order = SalesOrder(id=uuid.uuid4(), profile_id=1, profile="1")
        self.structural_root = _FakeLocation("Airport Road, Oshodi Store", structural=True)
        self.operational_leaf = _FakeLocation("Retail Shelf A", parent=self.structural_root)

    def test_goods_receipt_line_serializer_exposes_structural_location(self):
        line = SimpleNamespace(
            id=uuid.uuid4(),
            goods_receipt=SimpleNamespace(pk=uuid.uuid4()),
            purchase_order_line=None,
            inventory_item=SimpleNamespace(pk=uuid.uuid4(), name_snapshot="Nivea Cream"),
            stock_location=self.operational_leaf,
            received_quantity=Decimal("12"),
            unit_cost=Decimal("1500"),
            lot_number="LOT-001",
            manufactured_date=None,
            expiry_date=None,
            created_at=None,
        )

        payload = GoodsReceiptLineSerializer(line).data

        self.assertEqual(payload["location_name"], "Retail Shelf A")
        self.assertEqual(payload["structural_location_name"], "Airport Road, Oshodi Store")
        self.assertEqual(str(payload["structural_location_id"]), str(self.structural_root.id))

    def test_sales_order_shipment_line_serializer_exposes_structural_location(self):
        line = SimpleNamespace(
            id=uuid.uuid4(),
            sales_order_line=SimpleNamespace(pk=uuid.uuid4(), inventory_item=SimpleNamespace(name_snapshot="9PM Perfume")),
            stock_location=self.operational_leaf,
            stock_lot=None,
            stock_serial=None,
            reservation=None,
            quantity_shipped=Decimal("3"),
            notes="Packed for dispatch",
            created_at=None,
        )

        payload = SalesOrderShipmentLineSerializer(line).data

        self.assertEqual(payload["location_name"], "Retail Shelf A")
        self.assertEqual(payload["structural_location_name"], "Airport Road, Oshodi Store")
        self.assertEqual(str(payload["structural_location_id"]), str(self.structural_root.id))

    def test_goods_receipt_list_serializer_builds_structural_preview(self):
        serializer = GoodsReceiptListSerializer()
        receipt = SimpleNamespace(
            lines=_FakeLinesManager([
                SimpleNamespace(stock_location=self.operational_leaf),
                SimpleNamespace(stock_location=self.operational_leaf),
            ])
        )

        preview = serializer.get_structural_location_preview(receipt)

        self.assertEqual(preview, ["Airport Road, Oshodi Store"])

    def test_sales_order_shipment_list_serializer_builds_structural_preview(self):
        serializer = SalesOrderShipmentListSerializer()
        shipment = SimpleNamespace(
            lines=_FakeLinesManager([
                SimpleNamespace(stock_location=self.operational_leaf),
                SimpleNamespace(stock_location=self.operational_leaf),
            ])
        )

        preview = serializer.get_structural_location_preview(shipment)

        self.assertEqual(preview, ["Airport Road, Oshodi Store"])

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

    def test_purchase_line_rejects_quantity_below_inventory_reorder_quantity(self):
        self.inventory_item.reorder_quantity = Decimal("5")
        line_item = PurchaseOrderLineItem(
            purchase_order=self.purchase_order,
            inventory_item=self.inventory_item,
            quantity=3,
            unit_price=Decimal("9.00"),
        )

        with self.assertRaises(ValidationError):
            line_item.clean()

    def test_purchase_line_rejects_future_manufactured_date(self):
        self.inventory_item.reorder_quantity = Decimal("0")
        line_item = PurchaseOrderLineItem(
            purchase_order=self.purchase_order,
            inventory_item=self.inventory_item,
            quantity=3,
            unit_price=Decimal("9.00"),
            manufactured_date=date.today() + timedelta(days=1),
        )

        with self.assertRaises(ValidationError):
            line_item.clean()

    def test_purchase_line_rejects_non_future_expiry_date(self):
        self.inventory_item.reorder_quantity = Decimal("0")
        line_item = PurchaseOrderLineItem(
            purchase_order=self.purchase_order,
            inventory_item=self.inventory_item,
            quantity=3,
            unit_price=Decimal("9.00"),
            expiry_date=date.today(),
        )

        with self.assertRaises(ValidationError):
            line_item.clean()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST_USER="mailer@example.com",
    EMAIL_HOST_PASSWORD="secret",
    DEFAULT_FROM_EMAIL="Inventory <mailer@example.com>",
)
class PurchaseOrderEmailServiceTests(SimpleTestCase):
    @patch("subapps.services.emails.email_services.render_to_string", return_value="<p>Hello supplier</p>")
    def test_send_purchase_order_email_delivers_message_and_attachment(self, render_to_string_mock):
        purchase_order = SimpleNamespace(
            reference="PO-1001",
            profile=SimpleNamespace(name="DrabTech Softwares"),
            contact=SimpleNamespace(name="Alice", email="supplier@example.com"),
            supplier=SimpleNamespace(email="owner@example.com"),
            line_items=SimpleNamespace(all=lambda: []),
        )

        success = EmailService.send_purchase_order_email(
            purchase_order=purchase_order,
            pdf_file=BytesIO(b"%PDF-1.4 sample"),
        )

        self.assertTrue(success)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["supplier@example.com"])
        self.assertEqual(message.cc, ["owner@example.com"])
        self.assertEqual(message.from_email, "Inventory <mailer@example.com>")
        self.assertEqual(message.subject, "Purchase Order #PO-1001 from DrabTech Softwares")
        self.assertEqual(len(message.attachments), 1)
        self.assertEqual(message.attachments[0][0], "PurchaseOrder_PO-1001.pdf")
        render_to_string_mock.assert_called_once()

    def test_send_purchase_order_email_requires_contact_email(self):
        purchase_order = SimpleNamespace(
            reference="PO-1002",
            profile=SimpleNamespace(name="DrabTech Softwares"),
            contact=SimpleNamespace(name="Alice", email=""),
            supplier=None,
            line_items=SimpleNamespace(all=lambda: []),
        )

        success = EmailService.send_purchase_order_email(
            purchase_order=purchase_order,
            pdf_file=BytesIO(b"%PDF-1.4 sample"),
        )

        self.assertFalse(success)
        self.assertEqual(len(mail.outbox), 0)
