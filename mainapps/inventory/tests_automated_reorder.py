from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from mainapps.company.models import Company
from mainapps.inventory.models import (
    AutomatedReorderDecision,
    InventoryItem,
    InventoryPlacement,
)
from mainapps.orders.models import PurchaseOrder, PurchaseOrderStatus
from mainapps.stock.models import StockBalance, StockLocation
from subapps.services.automated_reorder import AutomatedReorderService
from subapps.services.operational_automations import OperationalAutomationService


class AutomatedReorderServiceTests(TestCase):
    def setUp(self):
        self.supplier = Company.objects.create(
            profile_id=1,
            name="Test Supplier",
            is_supplier=True,
        )
        self.location = StockLocation.objects.create(
            profile_id=1,
            name="Test Store",
            structural=True,
        )
        self.item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Test Reorder Item",
            status="active",
            purchaseable=True,
            automate_reorder=True,
            reorder_point=Decimal("10"),
            reorder_quantity=Decimal("25"),
            default_supplier=self.supplier,
            stock_uom_code="piece",
        )
        self.placement = InventoryPlacement.objects.create(
            profile_id=1,
            inventory_item=self.item,
            structural_location=self.location,
            active=True,
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=self.item,
            stock_location=self.location,
            quantity_on_hand=5,
            quantity_reserved=0,
        )

    @patch("subapps.services.automated_reorder.publish_workspace_notification")
    def test_creates_location_scoped_approval_gated_order_once(self, notify):
        result = AutomatedReorderService.evaluate(profile_id=1)

        self.assertEqual(result["created"], 1)
        order = PurchaseOrder.objects.get(profile_id=1)
        line = order.line_items.get()
        decision = AutomatedReorderDecision.objects.get(profile_id=1)

        self.assertEqual(order.status, PurchaseOrderStatus.PENDING)
        self.assertTrue(order.approval_required)
        self.assertEqual(order.workflow_state, "PENDING_APPROVAL")
        self.assertEqual(line.structural_location_id, self.location.id)
        self.assertEqual(line.quantity, 25)
        self.assertEqual(decision.purchase_order_id, order.id)
        notify.assert_called_once()

        second_result = AutomatedReorderService.evaluate(profile_id=1)
        self.assertEqual(second_result["created"], 0)
        self.assertEqual(PurchaseOrder.objects.filter(profile_id=1).count(), 1)
        self.assertEqual(
            AutomatedReorderDecision.objects.filter(profile_id=1).count(),
            1,
        )

    @patch("subapps.services.automated_reorder.publish_workspace_notification")
    def test_location_override_can_disable_item_policy(self, notify):
        self.placement.automate_reorder_override = False
        self.placement.save(update_fields=["automate_reorder_override"])

        result = AutomatedReorderService.evaluate(profile_id=1)

        self.assertEqual(result["created"], 0)
        self.assertFalse(PurchaseOrder.objects.filter(profile_id=1).exists())
        notify.assert_not_called()

    @patch(
        "subapps.services.operational_automations.publish_inventory_availability_upserted",
        return_value={"event_id": "availability-replay"},
    )
    def test_availability_reconciliation_replays_only_active_workspace_items(self, publish):
        inactive = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Archived Item",
            status="archived",
        )
        other_workspace = InventoryItem.objects.create(
            profile_id=2,
            name_snapshot="Other Workspace Item",
            status="active",
        )

        result = OperationalAutomationService._reconcile_inventory_availability(profile_id=1)

        self.assertEqual(result, {"reconciled": 1, "failed": 0})
        publish.assert_called_once_with(inventory_item_id=self.item.id)
        self.assertNotEqual(inactive.id, self.item.id)
        self.assertNotEqual(other_workspace.id, self.item.id)
