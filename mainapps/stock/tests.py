import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from mainapps.inventory.models import Inventory, InventoryCategory, InventoryItem
from mainapps.stock.models import StockItem
from mainapps.stock.views import (
    filter_inventory_items_for_legacy_inventory,
    filter_inventory_items_for_location,
    filter_inventory_items_for_purchase_order,
    filter_inventory_items_for_sales_order,
)
from subapps.services.inventory_read_model import (
    get_inventory_item_summary_map,
    get_location_stock_summary,
    get_profile_stock_analytics,
)
from subapps.services.stock_domain import StockDomainService


class StockItemLegacyBridgeTests(SimpleTestCase):
    def setUp(self):
        self.category = InventoryCategory(
            id=uuid.uuid4(),
            name="Components",
            profile_id=1,
            profile="1",
            default_location=None,
        )
        self.inventory = Inventory(
            id=uuid.uuid4(),
            name="Copper Wire",
            category=self.category,
            profile_id=1,
            profile="1",
            inventory_type="raw_material",
            re_order_point=10,
            re_order_quantity=200,
            minimum_stock_level=0,
            safety_stock_level=0,
            expiration_threshold=30,
        )
    def _mock_stock_item_queryset(self, *, last_item=None):
        queryset = MagicMock()
        queryset.order_by.return_value.last.return_value = last_item
        return queryset

    def test_stock_item_does_not_auto_link_legacy_bridge_inventory_item(self):
        stock_item = StockItem(
            inventory=self.inventory,
            name="Legacy Stock Row",
            quantity=Decimal("5"),
        )

        with patch("mainapps.stock.models.StockItem.objects.filter", return_value=self._mock_stock_item_queryset()):
            with patch("mainapps.stock.models.MPTTModel.save", autospec=True):
                stock_item.save()

        self.assertEqual(stock_item.inventory_id, self.inventory.id)
        self.assertFalse(hasattr(stock_item, "inventory_item_id"))


class StockViewLegacyLookupTests(SimpleTestCase):
    def test_filter_inventory_items_for_legacy_inventory_uses_metadata_lookup(self):
        queryset = MagicMock()

        with patch(
            "mainapps.stock.views.InventoryItem.legacy_bridge_id",
            side_effect=AssertionError("bridge lookup should not run"),
            create=True,
        ):
            filter_inventory_items_for_legacy_inventory(queryset, "legacy-inventory-id")

        queryset.filter.assert_called_once_with(metadata__legacy_inventory_id="legacy-inventory-id")

    def test_filter_inventory_items_for_location_uses_stock_balances_only(self):
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


class InventoryItemSummaryCompatibilityTests(SimpleTestCase):
    def test_inventory_item_summary_map_no_longer_uses_legacy_stock_item_fallback(self):
        inventory_item = InventoryItem(
            id=uuid.uuid4(),
            profile_id=1,
            name_snapshot="Copper Wire",
            inventory_type="raw_material",
        )

        balance_queryset = MagicMock()
        balance_queryset.select_related.return_value.order_by.return_value = []

        movement_queryset = MagicMock()
        movement_queryset.values.return_value.annotate.return_value = []

        serial_queryset = MagicMock()
        serial_queryset.values.return_value.annotate.return_value = []

        with patch("subapps.services.inventory_read_model.StockBalance.objects.filter", return_value=balance_queryset):
            with patch("subapps.services.inventory_read_model.StockMovement.objects.filter", return_value=movement_queryset):
                with patch("subapps.services.inventory_read_model.StockSerial.objects.filter", return_value=serial_queryset):
                    with patch(
                        "mainapps.stock.models.StockItem.objects.filter",
                        side_effect=AssertionError("legacy stock item fallback should not run"),
                    ):
                        summary_map = get_inventory_item_summary_map([inventory_item])

        summary = summary_map[inventory_item.id]
        self.assertEqual(summary["quantity"], Decimal("0"))
        self.assertEqual(summary["quantity_available"], Decimal("0"))
        self.assertEqual(summary["serial_count"], 0)

    def test_location_stock_summary_no_longer_uses_legacy_stock_items(self):
        location = MagicMock()
        location.stock_items.all.side_effect = AssertionError("legacy location fallback should not run")

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
        location.stock_items.all.assert_not_called()

    def test_profile_stock_analytics_no_longer_uses_legacy_stock_items(self):
        balances = MagicMock()
        balances.__iter__.return_value = iter([])

        balance_queryset = MagicMock()
        balance_queryset.select_related.return_value = balances

        with patch("subapps.services.inventory_read_model.StockBalance.objects.filter", return_value=balance_queryset):
            with patch(
                "mainapps.stock.models.StockItem.objects.filter",
                side_effect=AssertionError("legacy stock analytics fallback should not run"),
            ):
                analytics = get_profile_stock_analytics(profile_id=1)

        self.assertEqual(analytics["total_stock_items"], 0)
        self.assertEqual(analytics["total_locations"], 0)
        self.assertEqual(analytics["total_stock_value"], Decimal("0"))
        self.assertEqual(analytics["location_distribution"], [])
        self.assertEqual(
            analytics["aging_analysis"],
            {
                "0-30_days": 0,
                "31-90_days": 0,
                "91-365_days": 0,
                "over_1_year": 0,
            },
        )


class StockDomainCompatibilityTests(SimpleTestCase):
    def setUp(self):
        self.inventory = Inventory(
            id=uuid.uuid4(),
            name="Copper Wire",
            profile_id=1,
            profile="1",
            inventory_type="raw_material",
            re_order_point=Decimal("10"),
            re_order_quantity=Decimal("20"),
            minimum_stock_level=Decimal("0"),
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

    def test_ensure_inventory_item_uses_metadata_lookup_instead_of_bridge_id(self):
        filtered_queryset = MagicMock()
        filtered_queryset.order_by.return_value.first.return_value = self.inventory_item

        with patch(
            "subapps.services.stock_domain.InventoryItem.objects.filter",
            return_value=filtered_queryset,
        ) as inventory_item_filter:
            with patch(
                "subapps.services.stock_domain.StockDomainService._resolve_catalog_variant_projection",
                return_value=None,
            ):
                with patch(
                    "subapps.services.stock_domain.InventoryItem.legacy_bridge_id",
                    side_effect=AssertionError("bridge lookup should not run"),
                    create=True,
                ):
                    with patch("subapps.services.stock_domain.InventoryItem.save", autospec=True):
                        resolved = StockDomainService.ensure_inventory_item(
                            inventory=self.inventory,
                            actor_user_id=1,
                        )

        inventory_item_filter.assert_called_once_with(
            metadata__legacy_inventory_id=str(self.inventory.id)
        )
        self.assertEqual(resolved, self.inventory_item)

    def test_ensure_inventory_item_does_not_read_or_write_stock_item_bridge_field(self):
        filtered_queryset = MagicMock()
        filtered_queryset.order_by.return_value.first.return_value = self.inventory_item

        class GuardedStockItem:
            def __init__(self, inventory):
                self.inventory = inventory
                self.inventory_id = inventory.id
                self.sku = ""

            @property
            def inventory_item_id(self):
                raise AssertionError("legacy stock_item.inventory_item_id should not be used")

        with patch(
            "subapps.services.stock_domain.InventoryItem.objects.filter",
            return_value=filtered_queryset,
        ):
            with patch(
                "subapps.services.stock_domain.StockDomainService._resolve_catalog_variant_projection",
                return_value=None,
            ):
                with patch("subapps.services.stock_domain.InventoryItem.save", autospec=True):
                    resolved = StockDomainService.ensure_inventory_item(
                        stock_item=GuardedStockItem(self.inventory),
                        actor_user_id=1,
                    )

        self.assertEqual(resolved, self.inventory_item)

    def test_get_locked_balance_starts_from_zero_without_legacy_stock_seed(self):
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
                with patch(
                    "subapps.services.stock_domain.StockItem.objects.filter",
                    side_effect=AssertionError("legacy stock seed should not run"),
                ):
                    balance = StockDomainService._get_locked_balance(
                        profile_id=1,
                        inventory_item=self.inventory_item,
                        stock_location=stock_location,
                        legacy_inventory=self.inventory,
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

    def test_ensure_inventory_item_no_longer_uses_external_system_id_as_sku_snapshot(self):
        self.inventory.external_system_id = "INV-123"
        filtered_queryset = MagicMock()
        filtered_queryset.order_by.return_value.first.return_value = None

        with patch(
            "subapps.services.stock_domain.InventoryItem.objects.filter",
            return_value=filtered_queryset,
        ):
            with patch(
                "subapps.services.stock_domain.StockDomainService._resolve_catalog_variant_projection",
                return_value=None,
            ):
                with patch("subapps.services.stock_domain.InventoryItem.save", autospec=True):
                    resolved = StockDomainService.ensure_inventory_item(
                        inventory=self.inventory,
                        actor_user_id=5,
                    )

        self.assertEqual(resolved.sku_snapshot, "")

    def test_catalog_variant_projection_uses_stock_item_sku_without_touching_legacy_product_variant(self):
        variant = SimpleNamespace(
            variant_id=uuid.uuid4(),
            variant_barcode="barcode-123",
            variant_sku="SKU-123",
        )

        class GuardedStockItem:
            sku = "SKU-123"

            @property
            def product_variant(self):
                raise AssertionError("legacy stock_item.product_variant should not be used")

        queryset = MagicMock()

        def filter_side_effect(**kwargs):
            result = MagicMock()
            if kwargs == {"profile_id": 1}:
                return queryset
            if kwargs == {"variant_id": self.inventory_item.product_variant_id}:
                result.first.return_value = None
                return result
            if kwargs == {"variant_barcode": "SKU-123"}:
                result.first.return_value = None
                return result
            if kwargs == {"variant_sku": "SKU-123"}:
                result.first.return_value = variant
                return result
            result.first.return_value = None
            return result

        queryset.filter.side_effect = filter_side_effect

        with patch(
            "mainapps.projections.models.CatalogVariantProjection.objects.select_related",
            return_value=queryset,
        ):
            resolved = StockDomainService._resolve_catalog_variant_projection(
                profile_id=1,
                inventory_item=self.inventory_item,
                stock_item=GuardedStockItem(),
            )

        self.assertEqual(resolved, variant)

    def test_catalog_variant_projection_uses_purchase_line_stock_item_sku_without_touching_legacy_product_variant(self):
        variant = SimpleNamespace(
            variant_id=uuid.uuid4(),
            variant_barcode="barcode-456",
            variant_sku="SKU-456",
        )

        class GuardedStockItem:
            id = uuid.uuid4()
            sku = "SKU-456"

            @property
            def product_variant(self):
                raise AssertionError("legacy purchase-line stock_item.product_variant should not be used")

        purchase_order_line = SimpleNamespace(
            stock_item_id=GuardedStockItem.id,
            stock_item=GuardedStockItem(),
        )

        queryset = MagicMock()

        def filter_side_effect(**kwargs):
            result = MagicMock()
            if kwargs == {"profile_id": 1}:
                return queryset
            if kwargs == {"variant_id": self.inventory_item.product_variant_id}:
                result.first.return_value = None
                return result
            if kwargs == {"variant_barcode": "SKU-456"}:
                result.first.return_value = None
                return result
            if kwargs == {"variant_sku": "SKU-456"}:
                result.first.return_value = variant
                return result
            result.first.return_value = None
            return result

        queryset.filter.side_effect = filter_side_effect

        with patch(
            "mainapps.projections.models.CatalogVariantProjection.objects.select_related",
            return_value=queryset,
        ):
            resolved = StockDomainService._resolve_catalog_variant_projection(
                profile_id=1,
                inventory_item=self.inventory_item,
                purchase_order_line=purchase_order_line,
            )

        self.assertEqual(resolved, variant)
