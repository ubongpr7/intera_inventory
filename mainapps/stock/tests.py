import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from mainapps.inventory.models import InventoryItem
from mainapps.stock.views import (
    filter_inventory_items_for_location,
    filter_inventory_items_for_purchase_order,
    filter_inventory_items_for_sales_order,
)
from subapps.services.inventory_read_model import (
    get_inventory_item_summary_map,
    get_location_stock_summary,
    get_profile_stock_analytics,
)
from subapps.services.stock_domain import StockDomainError, StockDomainService


class StockViewFilterTests(SimpleTestCase):
    def test_filter_inventory_items_for_location_uses_stock_balances(self):
        queryset = MagicMock()
        filtered_queryset = MagicMock()
        queryset.filter.return_value = filtered_queryset

        filter_inventory_items_for_location(queryset, "location-id")

        queryset.filter.assert_called_once_with(stock_balances__stock_location_id="location-id")
        filtered_queryset.distinct.assert_called_once_with()

    def test_filter_inventory_items_for_purchase_order_uses_purchase_order_lines(self):
        queryset = MagicMock()
        filtered_queryset = MagicMock()
        queryset.filter.return_value = filtered_queryset

        filter_inventory_items_for_purchase_order(queryset, "purchase-order-id")

        queryset.filter.assert_called_once_with(purchase_order_lines__purchase_order_id="purchase-order-id")
        filtered_queryset.distinct.assert_called_once_with()

    def test_filter_inventory_items_for_sales_order_uses_sales_order_lines(self):
        queryset = MagicMock()
        filtered_queryset = MagicMock()
        queryset.filter.return_value = filtered_queryset

        filter_inventory_items_for_sales_order(queryset, "sales-order-id")

        queryset.filter.assert_called_once_with(sales_order_lines__sales_order_id="sales-order-id")
        filtered_queryset.distinct.assert_called_once_with()


class StockReadModelTests(SimpleTestCase):
    def test_inventory_item_summary_map_builds_from_balances(self):
        inventory_item = InventoryItem(
            id=uuid.uuid4(),
            profile_id=1,
            name_snapshot="Copper Wire",
            sku_snapshot="CW-001",
            inventory_type="raw_material",
        )

        balance = MagicMock(
            inventory_item_id=inventory_item.id,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("1"),
            quantity_available=Decimal("4"),
            stock_location_id="loc-1",
            stock_lot_id=None,
        )
        balance.stock_location.name = "Main Warehouse"

        balance_queryset = MagicMock()
        balance_queryset.select_related.return_value.order_by.return_value = [balance]

        movement_queryset = MagicMock()
        movement_queryset.values.return_value.annotate.return_value = []

        serial_queryset = MagicMock()
        serial_queryset.values.return_value.annotate.return_value = []

        with patch("subapps.services.inventory_read_model.StockBalance.objects.filter", return_value=balance_queryset):
            with patch("subapps.services.inventory_read_model.StockMovement.objects.filter", return_value=movement_queryset):
                with patch("subapps.services.inventory_read_model.StockSerial.objects.filter", return_value=serial_queryset):
                    summary = get_inventory_item_summary_map([inventory_item])[inventory_item.id]

        self.assertEqual(summary["quantity"], Decimal("5"))
        self.assertEqual(summary["quantity_reserved"], Decimal("1"))
        self.assertEqual(summary["quantity_available"], Decimal("4"))
        self.assertEqual(summary["location_name"], "Main Warehouse")

    def test_location_stock_summary_reads_from_stock_balances_only(self):
        location = MagicMock()
        balances = MagicMock()
        balances.__iter__.return_value = iter([])
        expiring_balances = MagicMock()
        expiring_balances.count.return_value = 0
        balances.filter.return_value = expiring_balances

        balance_queryset = MagicMock()
        balance_queryset.filter.return_value = balances
        location.stock_balances.select_related.return_value = balance_queryset

        summary = get_location_stock_summary(location)

        self.assertEqual(summary["total_items"], 0)
        self.assertEqual(summary["total_quantity"], Decimal("0"))
        self.assertEqual(summary["total_value"], Decimal("0"))
        self.assertEqual(summary["top_inventory_types"], [])
        self.assertEqual(summary["expiring_soon_count"], 0)

    def test_profile_stock_analytics_reads_from_balances_only(self):
        balances = MagicMock()
        balances.__iter__.return_value = iter([])

        balance_queryset = MagicMock()
        balance_queryset.select_related.return_value = balances

        with patch("subapps.services.inventory_read_model.StockBalance.objects.filter", return_value=balance_queryset):
            analytics = get_profile_stock_analytics(profile_id=1)

        self.assertEqual(analytics["total_inventory_items"], 0)
        self.assertEqual(analytics["total_locations"], 0)
        self.assertEqual(analytics["total_stock_value"], Decimal("0"))


class StockDomainTests(SimpleTestCase):
    def setUp(self):
        self.inventory_item = InventoryItem(
            id=uuid.uuid4(),
            profile_id=1,
            name_snapshot="Copper Wire",
            inventory_type="raw_material",
        )

    def test_ensure_inventory_item_returns_explicit_item(self):
        resolved = StockDomainService.ensure_inventory_item(inventory_item=self.inventory_item)
        self.assertEqual(resolved, self.inventory_item)

    def test_ensure_inventory_item_rejects_missing_item(self):
        with self.assertRaises(StockDomainError):
            StockDomainService.ensure_inventory_item()

    def test_get_locked_balance_starts_from_zero(self):
        stock_location = MagicMock()
        filtered_queryset = MagicMock()
        filtered_queryset.first.return_value = None
        select_for_update_queryset = MagicMock()
        select_for_update_queryset.filter.return_value = filtered_queryset

        created_balance = MagicMock()

        with patch(
            "subapps.services.stock_domain.StockBalance.objects.select_for_update",
            return_value=select_for_update_queryset,
        ):
            with patch(
                "subapps.services.stock_domain.StockBalance.objects.create",
                return_value=created_balance,
            ) as create_balance:
                balance = StockDomainService._get_locked_balance(
                    profile_id=1,
                    inventory_item=self.inventory_item,
                    stock_location=stock_location,
                    actor_user_id=7,
                )

        self.assertEqual(balance, created_balance)
        create_balance.assert_called_once_with(
            profile_id=1,
            inventory_item=self.inventory_item,
            stock_location=stock_location,
            stock_lot=None,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
            created_by_user_id=7,
            updated_by_user_id=7,
        )
