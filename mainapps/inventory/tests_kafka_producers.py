from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from subapps.kafka.producers.inventory_admin import publish_inventory_admin_event, serialize_stock_location
from subapps.kafka.producers.inventory import publish_inventory_availability_upserted
from subapps.kafka.producers.orders_admin import publish_order_admin_event, serialize_goods_receipt_line


class InventoryKafkaProducerTests(SimpleTestCase):
    def test_stock_location_serializer_exposes_shared_address_id(self):
        location = SimpleNamespace(
            id="location-1",
            profile_id=17,
            name="Main Warehouse",
            code="WAREHOUSE_17_1",
            description="",
            structural=True,
            external=False,
            is_default_structural_location=True,
            parent_id=None,
            parent=None,
            location_type_id=3,
            location_type=SimpleNamespace(name="Warehouse"),
            official_user_id=None,
            address_id="052ccf7b-2276-490b-a4e9-2a7ae02f54c1",
            physical_address="1 Example Street",
            created_by_user_id=9,
            updated_by_user_id=9,
        )

        payload = serialize_stock_location(location)

        self.assertEqual(payload["address_id"], "052ccf7b-2276-490b-a4e9-2a7ae02f54c1")
        self.assertEqual(payload["physical_address"], "1 Example Street")

    def test_goods_receipt_line_serializer_exposes_trace_fields(self):
        inventory_item = SimpleNamespace(
            id="inventory-1",
            name_snapshot="Arabica Beans",
            sku_snapshot="SKU-ARABICA",
            barcode_snapshot="BAR-ARABICA",
            product_variant_image_url="https://cdn.example.com/arabica.png",
        )
        purchase_order_line = SimpleNamespace(
            id="po-line-1",
            quantity_received="7.00000",
            remaining_quantity="3.00000",
            fully_received=False,
        )
        purchase_order = SimpleNamespace(reference="PO-17-20260630-0001")
        goods_receipt = SimpleNamespace(
            id="gr-1",
            reference="GR-17-20260630010101",
            purchase_order_id="po-1",
            purchase_order=purchase_order,
            profile_id=17,
        )
        stock_location = SimpleNamespace(name="Main Warehouse")
        goods_receipt_line = SimpleNamespace(
            id="gr-line-1",
            goods_receipt_id="gr-1",
            goods_receipt=goods_receipt,
            purchase_order_line_id="po-line-1",
            purchase_order_line=purchase_order_line,
            inventory_item_id="inventory-1",
            inventory_item=inventory_item,
            stock_location_id="loc-1",
            stock_location=stock_location,
            received_quantity="4.00000",
            unit_cost="12.50",
            lot_number="LOT-001",
            manufactured_date=None,
            expiry_date=None,
            created_by_user_id=9,
            updated_by_user_id=9,
        )

        payload = serialize_goods_receipt_line(goods_receipt_line)

        self.assertEqual(payload["goods_receipt_reference"], "GR-17-20260630010101")
        self.assertEqual(payload["purchase_order_reference"], "PO-17-20260630-0001")
        self.assertEqual(payload["inventory_barcode"], "BAR-ARABICA")
        self.assertEqual(payload["product_variant_image_url"], "https://cdn.example.com/arabica.png")
        self.assertEqual(payload["display_image"], "https://cdn.example.com/arabica.png")
        self.assertEqual(payload["stock_location_name"], "Main Warehouse")
        self.assertEqual(payload["quantity_received_to_date"], 7.0)
        self.assertEqual(payload["remaining_quantity"], 3.0)

    @patch("subapps.kafka.producers.inventory.publish_event")
    @patch("subapps.kafka.producers.inventory._build_availability_snapshot")
    @patch("subapps.kafka.producers.inventory.InventoryItem.objects")
    def test_inventory_availability_event_includes_audit_envelope(
        self,
        inventory_manager,
        build_snapshot,
        publish_event,
    ):
        inventory_item = SimpleNamespace(
            id="inv-1",
            created_by_user_id=12,
            updated_by_user_id=34,
        )
        inventory_manager.filter.return_value.first.return_value = inventory_item
        build_snapshot.return_value = {
            "variant_id": "variant-1",
            "profile_id": 7,
            "inventory_item_id": "inv-1",
            "inventory_name": "Arabica Beans",
            "variant_barcode": "ABC-123",
            "variant_sku": "SKU-123",
            "stock_status": "LOW_STOCK",
            "total_quantity": "2.000",
            "reserved_quantity": "0.000",
            "available_quantity": "2.000",
            "low_stock_threshold": 5,
            "location_breakdown": [],
        }

        publish_inventory_availability_upserted(inventory_item_id="inv-1")

        envelope_overrides = publish_event.call_args.kwargs["envelope_overrides"]
        self.assertEqual(envelope_overrides["workspace_id"], "7")
        self.assertEqual(envelope_overrides["actor"]["user_id"], "34")
        self.assertEqual(envelope_overrides["target"]["barcode"], "ABC-123")
        self.assertEqual(envelope_overrides["severity"], "warning")
        self.assertIn("low on stock", envelope_overrides["summary"])

    @patch("subapps.kafka.producers.inventory_admin.publish_audit_fact")
    def test_inventory_admin_event_uses_workspace_and_target_context(self, publish_audit_fact):
        payload = {
            "profile_id": 11,
            "inventory_item_id": "item-1",
            "name_snapshot": "Arabica Beans",
            "sku_snapshot": "SKU-11",
            "barcode_snapshot": "BAR-11",
        }

        publish_inventory_admin_event(
            event_name="inventory.item.updated",
            payload=payload,
            actor={"user_id": "55"},
            target={
                "type": "inventory_item",
                "id": "item-1",
                "label": "Arabica Beans",
                "barcode": "BAR-11",
                "sku": "SKU-11",
            },
            summary="Inventory item updated: Arabica Beans.",
            metadata={"status": "active"},
            before={"name_snapshot": "Old Beans"},
            after=payload,
            feature_area="inventory_master",
            reference_number="SKU-11",
        )

        call = publish_audit_fact.call_args.kwargs
        self.assertEqual(call["workspace_id"], "11")
        self.assertEqual(call["actor"]["user_id"], "55")
        self.assertEqual(call["target"]["type"], "inventory_item")
        self.assertEqual(call["feature_area"], "inventory_master")
        self.assertEqual(call["reference_number"], "SKU-11")

    @patch("subapps.kafka.producers.inventory_admin.publish_workspace_notification")
    @patch("subapps.kafka.producers.inventory_admin._notification_recipients")
    @patch("subapps.kafka.producers.inventory_admin.publish_audit_fact")
    def test_inventory_admin_event_can_publish_workspace_notification(
        self,
        publish_audit_fact,
        notification_recipients,
        publish_workspace_notification,
    ):
        payload = {
            "profile_id": 11,
            "inventory_item_id": "item-1",
            "name_snapshot": "Arabica Beans",
            "sku_snapshot": "SKU-11",
            "barcode_snapshot": "BAR-11",
            "updated_by_user_id": 55,
        }
        notification_recipients.return_value = (
            ["55", "88"],
            [
                {"user_id": "55", "user_email": "ops@example.com", "user_name": "Ops Lead"},
                {"user_id": "88", "user_email": "owner@example.com", "user_name": "Workspace Owner"},
            ],
        )

        publish_inventory_admin_event(
            event_name="inventory.stock.adjusted",
            payload=payload,
            actor={"user_id": "55"},
            target={
                "type": "inventory_item",
                "id": "item-1",
                "label": "Arabica Beans",
                "barcode": "BAR-11",
                "sku": "SKU-11",
            },
            summary="Stock adjusted for Arabica Beans.",
            metadata={"stock_location_name": "Main Warehouse", "quantity_change": -2},
            after=payload,
            feature_area="stock_control",
            reference_number="SKU-11",
            notification_category="stock_alert",
            notification_title="Stock adjusted for Arabica Beans",
            notification_message="Stock for Arabica Beans at Main Warehouse changed by -2.",
            notification_action_url="/inventory",
        )

        publish_audit_fact.assert_called_once()
        notification_recipients.assert_called_once()
        publish_workspace_notification.assert_called_once()
        notification_kwargs = publish_workspace_notification.call_args.kwargs
        self.assertEqual(notification_kwargs["event_name"], "notification.inventory.stock.adjusted")
        self.assertEqual(notification_kwargs["category"], "stock_alert")
        self.assertEqual(notification_kwargs["user_ids"], ["55", "88"])
        self.assertEqual(notification_kwargs["action_url"], "/inventory")
        self.assertEqual(
            notification_kwargs["metadata"]["affected_users"],
            [
                {"user_id": "55", "user_email": "ops@example.com", "user_name": "Ops Lead"},
                {"user_id": "88", "user_email": "owner@example.com", "user_name": "Workspace Owner"},
            ],
        )

    @patch("subapps.kafka.producers.orders_admin.publish_audit_fact")
    def test_order_admin_event_uses_workspace_and_reference_context(self, publish_audit_fact):
        payload = {
            "profile_id": 17,
            "purchase_order_id": "po-1",
            "reference": "PO-17-20260630-0001",
            "status": "approved",
        }

        publish_order_admin_event(
            event_name="purchase_order.approved",
            payload=payload,
            actor={"user_id": "91"},
            target={
                "type": "purchase_order",
                "id": "po-1",
                "label": "PO-17-20260630-0001",
            },
            summary="Purchase order approved: PO-17-20260630-0001.",
            metadata={"status": "approved"},
            after=payload,
            feature_area="purchasing",
            reference_number="PO-17-20260630-0001",
        )

        call = publish_audit_fact.call_args.kwargs
        self.assertEqual(call["workspace_id"], "17")
        self.assertEqual(call["actor"]["user_id"], "91")
        self.assertEqual(call["target"]["type"], "purchase_order")
        self.assertEqual(call["feature_area"], "purchasing")
        self.assertEqual(call["reference_number"], "PO-17-20260630-0001")

    @patch("subapps.kafka.producers.orders_admin.publish_workspace_notification")
    @patch("subapps.kafka.producers.orders_admin._notification_recipients")
    @patch("subapps.kafka.producers.orders_admin.publish_audit_fact")
    def test_order_admin_event_can_publish_workspace_notification(
        self,
        publish_audit_fact,
        notification_recipients,
        publish_workspace_notification,
    ):
        payload = {
            "profile_id": 17,
            "purchase_order_id": "po-1",
            "reference": "PO-17-20260630-0001",
            "status": "received",
            "responsible_user_id": 45,
        }
        notification_recipients.return_value = (
            ["45", "88"],
            [
                {"user_id": "45", "user_email": "ops@example.com", "user_name": "Ops Lead"},
                {"user_id": "88", "user_email": "owner@example.com", "user_name": "Workspace Owner"},
            ],
        )

        publish_order_admin_event(
            event_name="purchase_order.received",
            payload=payload,
            actor={"user_id": "91"},
            target={
                "type": "purchase_order",
                "id": "po-1",
                "label": "PO-17-20260630-0001",
            },
            summary="Purchase order received: PO-17-20260630-0001.",
            metadata={"status": "received"},
            after=payload,
            feature_area="purchasing",
            reference_number="PO-17-20260630-0001",
            notification_category="purchase_order",
            notification_title="Purchase order PO-17-20260630-0001 received",
            notification_message="Purchase order PO-17-20260630-0001 was marked received.",
            notification_action_url="/order/purchase",
        )

        publish_audit_fact.assert_called_once()
        notification_recipients.assert_called_once()
        publish_workspace_notification.assert_called_once()
        notification_kwargs = publish_workspace_notification.call_args.kwargs
        self.assertEqual(notification_kwargs["event_name"], "notification.purchase_order.received")
        self.assertEqual(notification_kwargs["category"], "purchase_order")
        self.assertEqual(notification_kwargs["user_ids"], ["45", "88"])
        self.assertEqual(notification_kwargs["action_url"], "/order/purchase")
        self.assertEqual(
            notification_kwargs["metadata"]["affected_users"],
            [
                {"user_id": "45", "user_email": "ops@example.com", "user_name": "Ops Lead"},
                {"user_id": "88", "user_email": "owner@example.com", "user_name": "Workspace Owner"},
            ],
        )
