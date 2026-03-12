import uuid
from decimal import Decimal
from unittest.mock import MagicMock, PropertyMock, patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from mainapps.inventory.models import Inventory, InventoryCategory, InventoryItem
from subapps.kafka.producers.inventory import _resolve_catalog_variant
from subapps.services.inventory_read_model import get_inventory_summary_map, get_low_stock_rows


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


class InventorySummaryCompatibilityTests(SimpleTestCase):
    def setUp(self):
        self.category = InventoryCategory(
            id=uuid.uuid4(),
            name="Consumables",
            profile_id=1,
            profile="1",
        )
        self.inventory = Inventory(
            id=uuid.uuid4(),
            name="Copper Wire",
            category=self.category,
            profile_id=1,
            profile="1",
            inventory_type="raw_material",
            minimum_stock_level=Decimal("0"),
            re_order_point=Decimal("10"),
            re_order_quantity=Decimal("20"),
            safety_stock_level=Decimal("0"),
            expiration_threshold=30,
        )
        self.inventory_item = InventoryItem(
            id=uuid.uuid4(),
            profile_id=1,
            name_snapshot=self.inventory.name,
            inventory_type=self.inventory.inventory_type,
            metadata={"legacy_inventory_id": str(self.inventory.id)},
        )

    def test_inventory_summary_map_uses_metadata_lookup_instead_of_bridge_id(self):
        balance = MagicMock(
            inventory_item_id=self.inventory_item.id,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("1"),
            quantity_available=Decimal("4"),
            stock_location_id=None,
            stock_lot_id=None,
        )
        balance_queryset = MagicMock()
        balance_queryset.select_related.return_value = [balance]

        movement_queryset = MagicMock()
        movement_queryset.values.return_value.annotate.return_value = []

        serial_queryset = MagicMock()
        serial_queryset.values.return_value.annotate.return_value = []

        with patch(
            "subapps.services.inventory_read_model.InventoryItem.objects.filter",
            return_value=[self.inventory_item],
        ) as inventory_item_filter:
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
                        with patch(
                            "subapps.services.inventory_read_model.InventoryItem.legacy_bridge_id",
                            side_effect=AssertionError("bridge lookup should not run"),
                            create=True,
                        ):
                            summary_map = get_inventory_summary_map([self.inventory])

        inventory_item_filter.assert_called_once_with(
            metadata__legacy_inventory_id__in=[str(self.inventory.id)]
        )
        summary = summary_map[self.inventory.id]
        self.assertEqual(summary["current_stock_level"], Decimal("5"))
        self.assertEqual(summary["quantity_reserved"], Decimal("1"))
        self.assertEqual(summary["quantity_available"], Decimal("4"))

    def test_inventory_stock_properties_delegate_to_summary_service(self):
        summary = {
            self.inventory.id: {
                "current_stock_level": Decimal("7"),
                "total_stock_value": Decimal("19.50"),
            }
        }

        with patch(
            "subapps.services.inventory_read_model.get_inventory_summary_map",
            return_value=summary,
        ) as summary_map:
            with patch(
                "mainapps.inventory.models.InventoryItem.legacy_bridge_id",
                side_effect=AssertionError("bridge lookup should not run"),
                create=True,
            ):
                self.assertEqual(self.inventory.current_stock_level, Decimal("7"))
                self.assertEqual(self.inventory.total_stock_value, Decimal("19.50"))

        self.assertEqual(summary_map.call_count, 2)

    def test_inventory_summary_map_no_longer_uses_legacy_stock_items_fallback(self):
        balance_queryset = MagicMock()
        balance_queryset.select_related.return_value = []

        movement_queryset = MagicMock()
        movement_queryset.values.return_value.annotate.return_value = []

        with patch(
            "subapps.services.inventory_read_model.InventoryItem.objects.filter",
            return_value=[],
        ):
            with patch(
                "subapps.services.inventory_read_model.StockBalance.objects.filter",
                return_value=balance_queryset,
            ):
                with patch(
                    "subapps.services.inventory_read_model.StockMovement.objects.filter",
                    return_value=movement_queryset,
                ):
                    with patch(
                        "subapps.services.inventory_read_model.StockSerial.objects.filter"
                    ) as serial_filter:
                        with patch.object(
                            Inventory,
                            "stock_items",
                            new_callable=PropertyMock,
                        ) as stock_items:
                            stock_items.side_effect = AssertionError(
                                "legacy stock item fallback should not run"
                            )
                            serial_queryset = MagicMock()
                            serial_queryset.values.return_value.annotate.return_value = []
                            serial_filter.return_value = serial_queryset

                            summary = get_inventory_summary_map([self.inventory])[self.inventory.id]

        self.assertEqual(summary["current_stock_level"], Decimal("0"))
        self.assertEqual(summary["quantity_available"], Decimal("0"))
        self.assertEqual(summary["total_stock_value"], Decimal("0"))
        self.assertEqual(summary["location_breakdown"], [])
        self.assertEqual(summary["expiring_lots"], [])

    def test_catalog_variant_resolution_no_longer_uses_legacy_stock_items(self):
        class InventoryItemStub:
            profile_id = 1
            product_variant_id = None
            barcode_snapshot = "barcode-123"
            metadata = {"legacy_variant_barcode": "legacy-barcode"}
            sku_snapshot = "sku-123"

            @property
            def legacy_stock_items(self):
                raise AssertionError("legacy stock items should not be consulted")

        variant_queryset = MagicMock()
        barcode_filter = MagicMock()
        barcode_filter.first.return_value = None
        uuid_filter = MagicMock()
        uuid_filter.first.return_value = None
        sku_filter = MagicMock()
        sku_filter.first.return_value = None

        def filter_side_effect(*args, **kwargs):
            if kwargs == {"profile_id": 1}:
                return variant_queryset
            if kwargs == {"variant_barcode": "barcode-123"}:
                return barcode_filter
            if kwargs == {"variant_barcode": "legacy-barcode"}:
                return barcode_filter
            if kwargs == {"variant_barcode": "sku-123"}:
                return barcode_filter
            if kwargs == {"variant_sku": "barcode-123"}:
                return sku_filter
            if kwargs == {"variant_sku": "legacy-barcode"}:
                return sku_filter
            if kwargs == {"variant_sku": "sku-123"}:
                return sku_filter
            raise AssertionError(f"Unexpected filter call: {kwargs}")

        variant_queryset.filter.side_effect = filter_side_effect

        manager = MagicMock()
        manager.select_related.return_value = manager
        manager.filter.side_effect = filter_side_effect

        with patch(
            "subapps.kafka.producers.inventory.CatalogVariantProjection.objects",
            manager,
        ):
            self.assertIsNone(_resolve_catalog_variant(InventoryItemStub()))

    def test_low_stock_rows_no_longer_uses_external_system_id_as_sku(self):
        self.inventory.external_system_id = "INV-123"
        self.inventory.minimum_stock_level = Decimal("5")

        with patch(
            "subapps.services.inventory_read_model.get_inventory_summary_map",
            return_value={
                self.inventory.id: {
                    "current_stock_level": Decimal("1"),
                }
            },
        ):
            rows = get_low_stock_rows([self.inventory])

        self.assertEqual(rows[0]["sku"], "")
