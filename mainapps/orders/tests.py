import uuid
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.template.loader import get_template, render_to_string
from django.utils import timezone

from mainapps.inventory.models import InventoryItem
from mainapps.orders.models import PurchaseOrder, PurchaseOrderLineItem, PurchaseOrderStatus, SalesOrder, SalesOrderLineItem, SalesOrderStatus
from mainapps.orders.serializers import (
    GoodsReceiptLineSerializer,
    GoodsReceiptListSerializer,
    ReceiveItemsSerializer,
    SalesOrderShipmentLineSerializer,
    SalesOrderShipmentListSerializer,
)
from mainapps.orders.views import PurchaseOrderViewSet, SalesOrderViewSet, _purchase_order_edit_lock_reason, _sales_order_edit_lock_reason
from mainapps.orders.views import _purchase_order_open_line_count
from subapps.kafka.producers.orders_admin import serialize_goods_receipt_line
from subapps.services.emails.email_services import EmailService, get_workspace_display_name
from subapps.services.pdf.pdf_service import PDFServiceUnavailableError
from subapps.services.pdf.notification_documents import (
    NotificationDocumentError,
    build_purchase_order_pdf_url,
    build_return_order_pdf_url,
    verify_purchase_order_pdf_token,
    verify_return_order_pdf_token,
)


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

    def test_goods_receipt_list_serializer_uses_prefetched_lines_for_summary(self):
        receipt = SimpleNamespace(
            _prefetched_objects_cache={
                'lines': [
                    SimpleNamespace(
                        received_quantity=Decimal('2'),
                        stock_location_id='location-1',
                        stock_location=self.operational_leaf,
                        inventory_item=SimpleNamespace(name_snapshot='Nivea Cream'),
                    ),
                    SimpleNamespace(
                        received_quantity=Decimal('3'),
                        stock_location_id='location-1',
                        stock_location=self.operational_leaf,
                        inventory_item=SimpleNamespace(name_snapshot='Nivea Cream'),
                    ),
                    SimpleNamespace(
                        received_quantity=Decimal('4'),
                        stock_location_id='location-2',
                        stock_location=_FakeLocation('Retail Shelf B', parent=self.structural_root),
                        inventory_item=SimpleNamespace(name_snapshot='Afnan 9PM'),
                    ),
                ]
            }
        )
        serializer = GoodsReceiptListSerializer()

        self.assertEqual(serializer.get_line_count(receipt), 3)
        self.assertEqual(serializer.get_total_quantity(receipt), '9')
        self.assertEqual(serializer.get_location_count(receipt), 2)
        self.assertEqual(serializer.get_inventory_preview(receipt), ['Nivea Cream', 'Afnan 9PM'])
        self.assertEqual(serializer.get_location_preview(receipt), ['Retail Shelf A', 'Retail Shelf B'])
        self.assertEqual(serializer.get_structural_location_preview(receipt), ['Airport Road, Oshodi Store'])

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


class PurchaseOrderReceiveItemsSerializerTests(SimpleTestCase):
    def test_receive_items_serializer_coerces_date_strings(self):
        serializer = ReceiveItemsSerializer(
            data={
                "received_items": [
                    {
                        "line_item_id": str(uuid.uuid4()),
                        "quantity_received": 14,
                        "location_id": str(uuid.uuid4()),
                        "lot_number": "137032326634",
                        "manufactured_date": "2026-01-20",
                        "expiry_date": "2027-04-23",
                    }
                ]
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        received_item = serializer.validated_data["received_items"][0]
        self.assertEqual(received_item["manufactured_date"], date(2026, 1, 20))
        self.assertEqual(received_item["expiry_date"], date(2027, 4, 23))


class PurchaseOrderAuditSerializationTests(SimpleTestCase):
    def test_serialize_goods_receipt_line_tolerates_string_dates(self):
        goods_receipt_line = SimpleNamespace(
            id=uuid.uuid4(),
            goods_receipt_id=uuid.uuid4(),
            goods_receipt=SimpleNamespace(
                reference="GR-1001",
                profile_id=4,
                purchase_order_id=uuid.uuid4(),
                purchase_order=SimpleNamespace(reference="PO-1001"),
            ),
            purchase_order_line_id=uuid.uuid4(),
            purchase_order_line=SimpleNamespace(
                quantity_received=Decimal("14"),
                remaining_quantity=Decimal("0"),
                fully_received=True,
            ),
            inventory_item_id=uuid.uuid4(),
            inventory_item=SimpleNamespace(
                name_snapshot="Afnan 9PM Eau De Parfum 100ml",
                sku_snapshot="AFNAN-9PM-100",
                barcode_snapshot="12345",
                product_variant_image_url="",
            ),
            stock_location_id=uuid.uuid4(),
            stock_location=SimpleNamespace(name="Main Store"),
            received_quantity=Decimal("14"),
            unit_cost=Decimal("36100.00"),
            lot_number="137032326634",
            manufactured_date="2026-01-20",
            expiry_date="2027-04-23",
            created_by_user_id=7,
            updated_by_user_id=7,
        )

        payload = serialize_goods_receipt_line(goods_receipt_line)

        self.assertEqual(payload["manufactured_date"], "2026-01-20")
        self.assertEqual(payload["expiry_date"], "2027-04-23")


class PurchaseOrderEditLockTests(SimpleTestCase):
    def test_issued_purchase_order_is_locked_for_edits(self):
        purchase_order = PurchaseOrder(
            id=uuid.uuid4(),
            profile_id=1,
            profile="1",
            status="issued",
            workflow_state="SENT_TO_SUPPLIER",
            issue_date=timezone.now(),
        )

        self.assertEqual(
            _purchase_order_edit_lock_reason(purchase_order),
            "Issued purchase orders can no longer be edited.",
        )

    def test_pending_purchase_order_remains_editable(self):
        purchase_order = PurchaseOrder(
            id=uuid.uuid4(),
            profile_id=1,
            profile="1",
            status="pending",
            workflow_state="DRAFT",
            issue_date=None,
        )

        self.assertIsNone(_purchase_order_edit_lock_reason(purchase_order))

    def test_completed_purchase_order_update_is_rejected(self):
        purchase_order = PurchaseOrder(
            id=uuid.uuid4(),
            profile_id=1,
            profile="1",
            status=PurchaseOrderStatus.COMPLETED,
            workflow_state="CLOSED",
            issue_date=timezone.now(),
        )
        view = PurchaseOrderViewSet()
        view.get_object = MagicMock(return_value=purchase_order)

        response = view.partial_update(request=None, pk="unused")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "Issued purchase orders can no longer be edited.")


class SalesOrderEditLockTests(SimpleTestCase):
    def test_completed_and_cancelled_sales_orders_are_locked_for_edits(self):
        for status in (SalesOrderStatus.COMPLETED, SalesOrderStatus.CANCELLED):
            sales_order = SalesOrder(id=uuid.uuid4(), profile_id=1, profile="1", status=status)

            self.assertEqual(
                _sales_order_edit_lock_reason(sales_order),
                "Completed or cancelled sales orders can no longer be edited.",
            )

    def test_in_progress_sales_order_remains_editable(self):
        sales_order = SalesOrder(id=uuid.uuid4(), profile_id=1, profile="1", status=SalesOrderStatus.IN_PROGRESS)

        self.assertIsNone(_sales_order_edit_lock_reason(sales_order))

    def test_completed_sales_order_partial_update_is_rejected(self):
        sales_order = SalesOrder(id=uuid.uuid4(), profile_id=1, profile="1", status=SalesOrderStatus.COMPLETED)
        view = SalesOrderViewSet()
        view.get_object = MagicMock(return_value=sales_order)

        response = view.partial_update(request=None, pk="unused")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "Completed or cancelled sales orders can no longer be edited.")


class PurchaseOrderOpenLineCountTests(SimpleTestCase):
    def test_purchase_order_workbench_responses_are_not_cached(self):
        self.assertFalse(PurchaseOrderViewSet.CACHE_ENABLED)

    def test_open_line_count_uses_non_fully_received_lines(self):
        purchase_order = SimpleNamespace(
            line_items=SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(fully_received=True),
                    SimpleNamespace(fully_received=False),
                    SimpleNamespace(fully_received=False),
                ]
            )
        )

        self.assertEqual(_purchase_order_open_line_count(purchase_order), 2)


class PurchaseOrderCompletionTests(SimpleTestCase):
    def test_complete_rejects_orders_with_unreceived_line_items(self):
        purchase_order = SimpleNamespace(
            status=PurchaseOrderStatus.RECEIVED,
            line_items=SimpleNamespace(
                filter=lambda **_kwargs: SimpleNamespace(count=lambda: 1),
            ),
        )
        view = PurchaseOrderViewSet()
        view.get_object = MagicMock(return_value=purchase_order)

        response = view.complete(request=None, pk="unused")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "All purchase order line items must be fully received before completion")


class PurchaseOrderPdfDownloadTests(SimpleTestCase):
    def test_purchase_order_template_compiles_without_custom_tag_library(self):
        self.assertIsNotNone(get_template("pdf/purchase_order.html"))

    def test_purchase_order_template_renders_string_currency_and_line_items(self):
        line_item = SimpleNamespace(
            inventory_item=SimpleNamespace(
                name_snapshot="QA polo shirt",
                stock_uom_code="piece",
                default_uom_code="piece",
                description="QA rendered line item",
            ),
            quantity=12,
            unit_price=Decimal("1.00"),
            discount=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total_price=Decimal("12.00"),
        )
        purchase_order = SimpleNamespace(
            reference="PO-1001",
            issue_date=date(2026, 8, 25),
            delivery_date=None,
            supplier=SimpleNamespace(name="QA Supplier"),
            contact=None,
            notes="",
            responsible="",
            total_price=Decimal("12.00"),
        )

        html = render_to_string(
            "pdf/purchase_order.html",
            {
                "po": purchase_order,
                "company_name": "QA Workspace",
                "company_logo_url": "",
                "company_address": "10 First Avenue\nLagos\nNigeria",
                "supplier_address": "",
                "shipping_address": "",
                "currency_code": "NGN",
                "line_items": [line_item],
                "subtotal": Decimal("12.00"),
                "discount": Decimal("0.00"),
                "tax": Decimal("0.00"),
                "valid_until": date(2026, 9, 24),
            },
        )

        self.assertIn("Unit Price (NGN)", html)
        self.assertIn("QA polo shirt", html)
        self.assertIn("10 First Avenue", html)
        self.assertNotIn("currency.code", html)

    @patch("mainapps.orders.views.PDFService.generate_purchase_order_pdf")
    def test_download_pdf_reports_renderer_unavailability(self, generate_pdf):
        generate_pdf.side_effect = PDFServiceUnavailableError("WeasyPrint native dependencies are unavailable.")
        view = PurchaseOrderViewSet()
        view.get_object = MagicMock(return_value=SimpleNamespace(reference="PO-1001"))

        response = view.download_pdf(request=None, pk="unused")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"], "WeasyPrint native dependencies are unavailable.")


@override_settings(
    NOTIFICATION_DOCUMENT_BASE_URL="https://inventory.example.com",
    NOTIFICATION_DOCUMENT_SIGNING_SALT="test-notification-document-salt",
    NOTIFICATION_DOCUMENT_URL_TTL_SECONDS=900,
)
class PurchaseOrderNotificationDocumentTests(SimpleTestCase):
    def test_signed_purchase_order_url_is_bound_to_its_purchase_order(self):
        purchase_order = SimpleNamespace(id=uuid.uuid4())
        url = build_purchase_order_pdf_url(purchase_order)
        token = parse_qs(urlparse(url).query)["token"][0]

        self.assertTrue(url.startswith("https://inventory.example.com/order_api/purchase-orders/"))
        verify_purchase_order_pdf_token(purchase_order_id=str(purchase_order.id), token=token)

    def test_signed_purchase_order_url_cannot_be_reused_for_a_different_order(self):
        purchase_order = SimpleNamespace(id=uuid.uuid4())
        token = parse_qs(urlparse(build_purchase_order_pdf_url(purchase_order)).query)["token"][0]

        with self.assertRaises(NotificationDocumentError):
            verify_purchase_order_pdf_token(purchase_order_id=str(uuid.uuid4()), token=token)


class PurchaseOrderNotificationDispatchTests(SimpleTestCase):
    @patch.object(PurchaseOrderViewSet, "_workspace_owner_recipient", return_value=None)
    @patch("mainapps.orders.views.publish_notification_dispatch")
    @patch("mainapps.orders.views.build_purchase_order_pdf_url", return_value="https://inventory.example.com/po.pdf?token=abc")
    def test_dispatch_falls_back_to_the_supplier_email_without_a_contact(self, build_document_url, publish_dispatch, _owner_recipient):
        purchase_order = SimpleNamespace(
            id=uuid.uuid4(),
            profile_id=1,
            reference="PO-1000",
            profile=SimpleNamespace(name="DrabTech Softwares"),
            contact=None,
            supplier=SimpleNamespace(name="Supplier", email="supplier@example.com"),
            delivery_date=None,
            order_currency="NGN",
        )

        PurchaseOrderViewSet()._publish_purchase_order_email_dispatch(
            purchase_order,
            delivery_key="purchase-order:0:issued",
            email_required=True,
        )

        build_document_url.assert_called_once_with(purchase_order)
        payload = publish_dispatch.call_args.kwargs["payload"]
        self.assertEqual(payload["recipients"], [{
            "kind": "external_email",
            "email": "supplier@example.com",
            "display_name": "Supplier",
        }])

    @patch.object(
        PurchaseOrderViewSet,
        "_workspace_owner_recipient",
        return_value={
            "kind": "user",
            "user_id": "owner-7",
            "email_snapshot": "owner@example.com",
            "display_name": "Workspace Owner",
        },
    )
    @patch("mainapps.orders.views.publish_notification_dispatch")
    @patch("mainapps.orders.views.build_purchase_order_pdf_url", return_value="https://inventory.example.com/po.pdf?token=abc")
    def test_dispatch_copies_the_pdf_to_the_workspace_owner(self, build_document_url, publish_dispatch, _owner_recipient):
        purchase_order = SimpleNamespace(
            id=uuid.uuid4(),
            profile_id=1,
            reference="PO-1000-owner-copy",
            profile=SimpleNamespace(name="DrabTech Softwares"),
            contact=None,
            supplier=SimpleNamespace(name="Supplier", email="supplier@example.com"),
            delivery_date=None,
            order_currency="NGN",
        )

        PurchaseOrderViewSet()._publish_purchase_order_email_dispatch(
            purchase_order,
            delivery_key="purchase-order:owner-copy:issued",
            email_required=True,
        )

        build_document_url.assert_called_once_with(purchase_order)
        payload = publish_dispatch.call_args.kwargs["payload"]
        self.assertEqual(payload["recipients"], [
            {
                "kind": "external_email",
                "email": "supplier@example.com",
                "display_name": "Supplier",
            },
            {
                "kind": "user",
                "user_id": "owner-7",
                "email_snapshot": "owner@example.com",
                "display_name": "Workspace Owner",
            },
        ])
        self.assertEqual(len(payload["attachments"]), 1)

    @patch.object(PurchaseOrderViewSet, "_workspace_owner_recipient", return_value=None)
    @patch("mainapps.orders.views.publish_notification_dispatch")
    @patch("mainapps.orders.views.build_purchase_order_pdf_url", return_value="https://inventory.example.com/po.pdf?token=abc")
    def test_shadow_dispatch_disables_external_email(self, build_document_url, publish_dispatch, _owner_recipient):
        purchase_order = SimpleNamespace(
            id=uuid.uuid4(),
            profile_id=1,
            reference="PO-1001",
            profile=SimpleNamespace(name="DrabTech Softwares"),
            contact=SimpleNamespace(name="Alice", email="supplier@example.com"),
            supplier=SimpleNamespace(name="Supplier", email="owner@example.com"),
            delivery_date=date(2026, 8, 31),
            order_currency="NGN",
        )

        PurchaseOrderViewSet()._publish_purchase_order_email_dispatch(
            purchase_order,
            delivery_key="purchase-order:1:issued",
            email_required=False,
        )

        build_document_url.assert_called_once_with(purchase_order)
        payload = publish_dispatch.call_args.kwargs["payload"]
        self.assertEqual(payload["channels"]["email"], "disabled")
        self.assertEqual(payload["template"]["key"], "purchase_order_issued")
        self.assertEqual(payload["template"]["data"]["line_items"], [])
        self.assertEqual(payload["email_thread"], {"key": f"purchase-order:{purchase_order.id}", "is_reply": False})
        self.assertEqual(len(payload["attachments"]), 1)

    @patch.object(PurchaseOrderViewSet, "_workspace_owner_recipient", return_value=None)
    @patch("mainapps.orders.views.publish_notification_dispatch")
    @patch("mainapps.orders.views.build_purchase_order_pdf_url", return_value="https://inventory.example.com/po.pdf?token=abc")
    def test_dispatch_includes_purchase_order_line_items(self, build_document_url, publish_dispatch, _owner_recipient):
        line_item = SimpleNamespace(
            inventory_item="Navy polo shirt",
            description="Medium",
            quantity=4,
            unit_price=Decimal("2500.00"),
            total_price=Decimal("10000.00"),
        )
        purchase_order = SimpleNamespace(
            id=uuid.uuid4(),
            profile_id=1,
            reference="PO-1002",
            profile=SimpleNamespace(name="DrabTech Softwares"),
            contact=SimpleNamespace(name="Alice", email="supplier@example.com"),
            supplier=SimpleNamespace(name="Supplier", email="owner@example.com"),
            delivery_date=date(2026, 8, 31),
            order_currency="NGN",
            line_items=SimpleNamespace(
                select_related=lambda *_args: SimpleNamespace(all=lambda: [line_item]),
            ),
        )

        PurchaseOrderViewSet()._publish_purchase_order_email_dispatch(
            purchase_order,
            delivery_key="purchase-order:2:issued",
            email_required=True,
        )

        build_document_url.assert_called_once_with(purchase_order)
        payload = publish_dispatch.call_args.kwargs["payload"]
        self.assertEqual(payload["channels"]["email"], "required")
        self.assertEqual(payload["template"]["data"]["order_currency"], "NGN")
        self.assertEqual(payload["email_thread"], {"key": f"purchase-order:{purchase_order.id}", "is_reply": False})
        self.assertEqual(
            payload["template"]["data"]["line_items"],
            [{
                "name": "Navy polo shirt",
                "description": "Medium",
                "quantity": "4",
                "unit_price": "2,500.00",
                "line_total": "10,000.00",
            }],
        )

    @override_settings(PURCHASE_ORDER_EMAIL_DELIVERY_MODE="shadow")
    @patch.object(PurchaseOrderViewSet, "_publish_purchase_order_email_dispatch")
    @patch("mainapps.orders.views.EmailService.send_purchase_order_email")
    def test_shadow_mode_does_not_send_the_legacy_purchase_order_email(self, send_email, publish_dispatch):
        purchase_order = SimpleNamespace(id=uuid.uuid4(), reference="PO-1002")

        PurchaseOrderViewSet()._send_purchase_order_email(purchase_order)

        publish_dispatch.assert_called_once_with(
            purchase_order,
            delivery_key=f"purchase-order:{purchase_order.id}:issued",
            email_required=False,
        )
        send_email.assert_not_called()


@override_settings(
    NOTIFICATION_DOCUMENT_BASE_URL="https://inventory.example.com",
    NOTIFICATION_DOCUMENT_SIGNING_SALT="test-notification-document-salt",
    NOTIFICATION_DOCUMENT_URL_TTL_SECONDS=900,
)
class ReturnOrderNotificationDocumentTests(SimpleTestCase):
    def test_signed_return_order_url_is_bound_to_its_return_order(self):
        return_order = SimpleNamespace(id=uuid.uuid4())
        url = build_return_order_pdf_url(return_order)
        token = parse_qs(urlparse(url).query)["token"][0]

        self.assertTrue(url.startswith("https://inventory.example.com/order_api/return-orders/"))
        verify_return_order_pdf_token(return_order_id=str(return_order.id), token=token)

    def test_signed_return_order_url_cannot_be_reused_for_a_different_order(self):
        return_order = SimpleNamespace(id=uuid.uuid4())
        token = parse_qs(urlparse(build_return_order_pdf_url(return_order)).query)["token"][0]

        with self.assertRaises(NotificationDocumentError):
            verify_return_order_pdf_token(return_order_id=str(uuid.uuid4()), token=token)


class ReturnOrderNotificationDispatchTests(SimpleTestCase):
    @patch("mainapps.orders.views.publish_notification_dispatch")
    @patch("mainapps.orders.views.build_return_order_pdf_url", return_value="https://inventory.example.com/return.pdf?token=def")
    @patch("mainapps.orders.views.build_purchase_order_pdf_url", return_value="https://inventory.example.com/po.pdf?token=abc")
    def test_shadow_dispatch_disables_external_email_and_deduplicates_recipients(
        self,
        build_purchase_order_url,
        build_return_order_url,
        publish_dispatch,
    ):
        purchase_order = SimpleNamespace(
            id=uuid.uuid4(),
            reference="PO-1001",
            supplier=SimpleNamespace(name="Supplier", email="supplier@example.com"),
        )
        return_order = SimpleNamespace(
            id=uuid.uuid4(),
            profile_id=1,
            profile=SimpleNamespace(name="DrabTech Softwares"),
            reference="RO-1001",
            contact=SimpleNamespace(name="Alice", email="SUPPLIER@example.com"),
        )

        PurchaseOrderViewSet()._publish_return_order_email_dispatch(
            return_order,
            purchase_order,
            delivery_key="return-order:1:created",
            email_required=False,
        )

        build_purchase_order_url.assert_called_once_with(purchase_order)
        build_return_order_url.assert_called_once_with(return_order)
        payload = publish_dispatch.call_args.kwargs["payload"]
        self.assertEqual(payload["channels"]["email"], "disabled")
        self.assertEqual(payload["template"]["key"], "return_order_created")
        self.assertEqual(len(payload["recipients"]), 1)
        self.assertEqual(len(payload["attachments"]), 2)

    @override_settings(RETURN_ORDER_EMAIL_DELIVERY_MODE="shadow")
    @patch.object(PurchaseOrderViewSet, "_publish_return_order_email_dispatch")
    @patch("mainapps.orders.views.EmailService.send_return_order_email")
    def test_shadow_mode_does_not_send_the_legacy_return_order_email(self, send_email, publish_dispatch):
        purchase_order = SimpleNamespace(id=uuid.uuid4(), reference="PO-1002")
        return_order = SimpleNamespace(id=uuid.uuid4(), reference="RO-1002")

        PurchaseOrderViewSet()._send_return_order_email(return_order, purchase_order)

        publish_dispatch.assert_called_once_with(
            return_order,
            purchase_order,
            delivery_key=f"return-order:{return_order.id}:created",
            email_required=False,
        )
        send_email.assert_not_called()


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

    @patch("subapps.services.emails.email_services.render_to_string", return_value="<p>Return request</p>")
    def test_send_return_order_email_supports_legacy_string_profile(self, render_to_string_mock):
        purchase_order = SimpleNamespace(
            reference="PO-1003",
            supplier=SimpleNamespace(name="Supplier", email="supplier@example.com"),
        )
        return_order = SimpleNamespace(
            reference="RO-1003",
            profile="77",
            profile_id=None,
            purchase_order=purchase_order,
            contact=SimpleNamespace(name="Alice", email=""),
        )

        success = EmailService.send_return_order_email(
            return_order=return_order,
            po_pdf=BytesIO(b"%PDF-1.4 purchase order"),
            return_pdf=BytesIO(b"%PDF-1.4 return order"),
        )

        self.assertTrue(success)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(render_to_string_mock.call_args.args[1]["company_name"], "Company")


class WorkspaceDisplayNameTests(TestCase):
    def test_legacy_string_profile_uses_identity_projection_display_name(self):
        from mainapps.identity.models import IdentityCompanyProfile

        IdentityCompanyProfile.objects.create(
            profile_id=77,
            company_code="QA-WORKSPACE-77",
            display_name="QA Workspace",
        )

        order = SimpleNamespace(profile="77", profile_id=77)

        self.assertEqual(get_workspace_display_name(order), "QA Workspace")
