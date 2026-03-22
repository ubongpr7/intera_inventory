from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from mainapps.inventory.models import InventoryCategory, InventoryItem
from subapps.kafka.producers.inventory import _resolve_catalog_variant
from subapps.services.inventory_read_model import get_inventory_item_summary_map, get_low_stock_rows


class InventoryCategoryConstraintTests(TestCase):
    def test_same_category_name_is_allowed_across_profiles(self):
        InventoryCategory.objects.create(name="Consumables", profile_id=1)
        InventoryCategory.objects.create(name="Consumables", profile_id=2)

        self.assertEqual(
            InventoryCategory.objects.filter(name="Consumables").count(),
            2,
        )

    def test_same_category_name_is_rejected_within_same_profile(self):
        InventoryCategory.objects.create(name="Consumables", profile_id=1)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InventoryCategory.objects.create(name="Consumables", profile_id=1)


class InventoryItemSummaryTests(SimpleTestCase):
    def setUp(self):
        self.inventory_item = InventoryItem(
            id="item-1",
            profile_id=1,
            name_snapshot="Copper Wire",
            sku_snapshot="CW-001",
            barcode_snapshot="BC-001",
            inventory_type="raw_material",
            minimum_stock_level=Decimal("2"),
            reorder_point=Decimal("5"),
            reorder_quantity=Decimal("10"),
        )

    def test_inventory_summary_map_uses_inventory_item_balances(self):
        balance = MagicMock(
            inventory_item_id=self.inventory_item.id,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("1"),
            quantity_available=Decimal("4"),
            stock_location_id="loc-1",
            stock_lot_id=None,
        )
        balance.stock_location.name = "Main Warehouse"
        balance_queryset = MagicMock()
        balance_queryset.select_related.return_value = [balance]

        movement_queryset = MagicMock()
        movement_queryset.values.return_value.annotate.return_value = []

        serial_queryset = MagicMock()
        serial_queryset.values.return_value.annotate.return_value = []

        with patch(
            "subapps.services.inventory_read_model.StockBalance.objects.filter",
            return_value=balance_queryset,
        ):
            with patch(
                "subapps.services.inventory_read_model.StockMovement.objects.filter",
                return_value=movement_queryset,
            ):
                with patch(
                    "subapps.services.inventory_read_model.StockSerial.objects.filter",
                    return_value=serial_queryset,
                ):
                    summary_map = get_inventory_item_summary_map([self.inventory_item])

        summary = summary_map[self.inventory_item.id]
        self.assertEqual(summary["quantity"], Decimal("5"))
        self.assertEqual(summary["quantity_reserved"], Decimal("1"))
        self.assertEqual(summary["quantity_available"], Decimal("4"))
        self.assertEqual(summary["location_name"], "Main Warehouse")

    def test_low_stock_rows_use_inventory_item_snapshots(self):
        with patch(
            "subapps.services.inventory_read_model.get_inventory_item_summary_map",
            return_value={self.inventory_item.id: {"quantity": Decimal("1")}},
        ):
            rows = get_low_stock_rows([self.inventory_item])

        self.assertEqual(rows[0]["name"], "Copper Wire")
        self.assertEqual(rows[0]["sku"], "CW-001")

    def test_catalog_variant_resolution_does_not_require_legacy_inventory_bridge(self):
        variant_queryset = MagicMock()
        barcode_filter = MagicMock()
        barcode_filter.first.return_value = None
        sku_filter = MagicMock()
        sku_filter.first.return_value = None

        def filter_side_effect(*args, **kwargs):
            if kwargs == {"profile_id": 1}:
                return variant_queryset
            if kwargs in (
                {"variant_barcode": "BC-001"},
                {"variant_barcode": "CW-001"},
                {"variant_sku": "BC-001"},
                {"variant_sku": "CW-001"},
            ):
                return barcode_filter if "variant_barcode" in kwargs else sku_filter
            raise AssertionError(f"Unexpected filter call: {kwargs}")

        variant_queryset.filter.side_effect = filter_side_effect

        manager = MagicMock()
        manager.select_related.return_value = manager
        manager.filter.side_effect = filter_side_effect

        with patch(
            "subapps.kafka.producers.inventory.CatalogVariantProjection.objects",
            manager,
        ):
            self.assertIsNone(_resolve_catalog_variant(self.inventory_item))
