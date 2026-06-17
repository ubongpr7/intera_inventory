from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from mainapps.inventory.models import InventoryCategory, InventoryItem
from mainapps.projections.models import CatalogProductProjection, CatalogVariantProjection
from mainapps.inventory.views import InventoryItemViewSet, get_inventory_setup_summary
from subapps.kafka.consumers.catalog import handle_catalog_variant_event
from subapps.kafka.producers.inventory import _resolve_catalog_variant
from subapps.services.inventory_read_model import get_inventory_item_summary_map, get_low_stock_rows
from subapps.utils.request_context import scope_queryset_by_identity


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


class IdentityScopeTests(TestCase):
    def test_scope_queryset_ignores_missing_legacy_field_for_profile_id_only_models(self):
        matching_item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Copper Wire",
        )
        InventoryItem.objects.create(
            profile_id=2,
            name_snapshot="Steel Rod",
        )

        scoped = scope_queryset_by_identity(
            InventoryItem.objects.order_by("name_snapshot"),
            canonical_field="profile_id",
            legacy_field="profile",
            value=1,
        )

        self.assertEqual(list(scoped.values_list("id", flat=True)), [matching_item.id])


class CatalogVariantInventoryLinkTests(TestCase):
    def _event(self, *, track_stock=True, event_name="catalog.variant.upserted", variant_name="Phone - Black"):
        return {
            "event_name": event_name,
            "payload": {
                "variant_id": "11111111-1111-1111-1111-111111111111",
                "product_id": "22222222-2222-2222-2222-222222222222",
                "profile_id": 7,
                "display_name": variant_name,
                "variant_name": variant_name,
                "variant_barcode": "BAR-111",
                "variant_sku": "SKU-111",
                "image_url": "https://example.com/phone.jpg",
                "sales_price": "1250.00",
                "is_active": True,
                "pos_visible": True,
                "product": {
                    "product_id": "22222222-2222-2222-2222-222222222222",
                    "profile_id": 7,
                    "name": "Smart Phone",
                    "category_name": "Phones",
                    "tax_rate": "0.00",
                    "track_stock": track_stock,
                    "quick_sale": False,
                    "is_active": True,
                },
            },
        }

    @patch("subapps.kafka.consumers.catalog.publish_inventory_availability_upserted")
    def test_catalog_variant_event_creates_linked_inventory_item(self, publish_availability):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(handle_catalog_variant_event(self._event()))

        variant = CatalogVariantProjection.objects.get()
        item = InventoryItem.objects.get(profile_id=7, product_variant_id=variant.variant_id)

        self.assertEqual(item.product_template_id, variant.product_id)
        self.assertEqual(item.name_snapshot, "Phone - Black")
        self.assertEqual(item.sku_snapshot, "SKU-111")
        self.assertEqual(item.barcode_snapshot, "BAR-111")
        self.assertEqual(item.product_variant_image_url, "https://example.com/phone.jpg")
        self.assertEqual(item.inventory_type, "finished_good")
        self.assertTrue(item.track_stock)
        self.assertEqual(item.status, "active")
        self.assertEqual(item.metadata["source"], "catalog_variant_projection")
        publish_availability.assert_called_once_with(inventory_item_id=item.id)

    @patch("subapps.kafka.consumers.catalog.publish_inventory_availability_upserted")
    def test_catalog_variant_event_updates_existing_inventory_item_without_duplicates(self, publish_availability):
        with self.captureOnCommitCallbacks(execute=True):
            handle_catalog_variant_event(self._event())
        with self.captureOnCommitCallbacks(execute=True):
            handle_catalog_variant_event(self._event(variant_name="Phone - Midnight Black"))

        item = InventoryItem.objects.get(profile_id=7, product_variant_id="11111111-1111-1111-1111-111111111111")

        self.assertEqual(InventoryItem.objects.count(), 1)
        self.assertEqual(item.name_snapshot, "Phone - Midnight Black")
        self.assertEqual(item.product_variant_image_url, "https://example.com/phone.jpg")
        self.assertEqual(publish_availability.call_count, 2)

    @patch("subapps.kafka.consumers.catalog.publish_inventory_availability_upserted")
    def test_catalog_variant_event_does_not_create_inventory_item_when_product_does_not_track_stock(self, publish_availability):
        self.assertTrue(handle_catalog_variant_event(self._event(track_stock=False)))

        self.assertEqual(InventoryItem.objects.count(), 0)
        self.assertEqual(CatalogProductProjection.objects.get().track_stock, False)
        publish_availability.assert_not_called()

    @patch("subapps.kafka.consumers.catalog.publish_inventory_availability_upserted")
    def test_catalog_variant_deleted_event_does_not_create_inventory_item(self, publish_availability):
        self.assertTrue(handle_catalog_variant_event(self._event(event_name="catalog.variant.deleted")))

        self.assertEqual(InventoryItem.objects.count(), 0)
        publish_availability.assert_not_called()

    def test_scope_queryset_keeps_legacy_lookup_when_model_still_has_profile_field(self):
        matching_category = InventoryCategory.objects.create(name="Consumables", profile_id=1)
        InventoryCategory.objects.create(name="Electronics", profile_id=2)

        scoped = scope_queryset_by_identity(
            InventoryCategory.objects.order_by("name"),
            canonical_field="profile_id",
            legacy_field="profile",
            value=1,
        )

        self.assertEqual(list(scoped.values_list("id", flat=True)), [matching_category.id])

    def test_scope_queryset_supports_nested_identity_paths(self):
        matching_category = InventoryCategory.objects.create(name="Consumables", profile_id=1)
        other_category = InventoryCategory.objects.create(name="Electronics", profile_id=2)
        matching_item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Copper Wire",
            inventory_category=matching_category,
        )
        InventoryItem.objects.create(
            profile_id=2,
            name_snapshot="Steel Rod",
            inventory_category=other_category,
        )

        scoped = scope_queryset_by_identity(
            InventoryItem.objects.order_by("name_snapshot"),
            canonical_field="inventory_category__profile_id",
            legacy_field="inventory_category__profile",
            value=1,
        )

        self.assertEqual(list(scoped.values_list("id", flat=True)), [matching_item.id])


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
        balance_queryset.select_related.return_value.order_by.return_value = [balance]

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


class InventorySetupSummaryTests(SimpleTestCase):
    def test_inventory_setup_summary_uses_scoped_counts_and_stock_analytics(self):
        category_queryset = MagicMock()
        category_queryset.count.return_value = 4
        location_queryset = MagicMock()
        location_queryset.count.return_value = 7
        inventory_queryset = MagicMock()
        inventory_queryset.count.return_value = 11

        with patch(
            "mainapps.inventory.views.scope_queryset_by_identity",
            side_effect=[category_queryset, location_queryset, inventory_queryset],
        ) as scope_queryset:
            with patch(
                "mainapps.inventory.views.get_profile_stock_analytics",
                return_value={"total_stock_value": Decimal("9850.50")},
            ) as stock_analytics:
                with patch(
                    "mainapps.inventory.views.get_low_stock_rows",
                    return_value=[{"id": "a"}, {"id": "b"}],
                ) as low_stock_rows:
                    summary = get_inventory_setup_summary(profile_id=9)

        self.assertEqual(summary["total_categories"], 4)
        self.assertEqual(summary["total_locations"], 7)
        self.assertEqual(summary["total_inventory_items"], 11)
        self.assertEqual(summary["total_stock_value"], Decimal("9850.50"))
        self.assertEqual(summary["low_stock_count"], 2)
        self.assertEqual(scope_queryset.call_count, 3)
        stock_analytics.assert_called_once_with(profile_id=9)
        low_stock_rows.assert_called_once_with(inventory_queryset)


class InventoryBulkUpdateControlsTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_bulk_update_controls_updates_items_by_inventory_type(self):
        finished_good = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Phone",
            inventory_type="finished_good",
        )
        raw_material = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Copper Wire",
            inventory_type="raw_material",
        )
        InventoryItem.objects.create(
            profile_id=2,
            name_snapshot="Other Workspace Item",
            inventory_type="finished_good",
        )

        request = self.factory.post(
            "/inventory/items/bulk_update_controls/",
            {
                "updates": [
                    {
                        "inventory_type": "finished_good",
                        "minimum_stock_level": "20",
                        "reorder_point": "40",
                        "reorder_quantity": "200",
                    },
                    {
                        "inventory_type": "raw_material",
                        "minimum_stock_level": "10",
                        "reorder_point": "10",
                        "reorder_quantity": "50",
                    },
                ]
            },
            format="json",
        )
        force_authenticate(
            request,
            user=None,
            token={"profile_id": 1, "user_id": 1, "owner_id": 1, "permissions": ["manage_inventory_item_settings"]},
        )

        response = InventoryItemViewSet.as_view({"post": "bulk_update_controls"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated_count"], 2)
        self.assertEqual(response.data["skipped_count"], 0)

        finished_good.refresh_from_db()
        raw_material.refresh_from_db()
        self.assertEqual(finished_good.minimum_stock_level, Decimal("20"))
        self.assertEqual(finished_good.reorder_point, Decimal("40"))
        self.assertEqual(finished_good.reorder_quantity, Decimal("200"))
        self.assertEqual(raw_material.minimum_stock_level, Decimal("10"))
        self.assertEqual(raw_material.reorder_point, Decimal("10"))
        self.assertEqual(raw_material.reorder_quantity, Decimal("50"))

    def test_bulk_update_controls_skips_items_with_existing_thresholds_when_requested(self):
        InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Configured Item",
            inventory_type="finished_good",
            reorder_point=Decimal("5"),
        )

        request = self.factory.post(
            "/inventory/items/bulk_update_controls/",
            {
                "updates": [
                    {
                        "inventory_type": "finished_good",
                        "minimum_stock_level": "20",
                        "reorder_point": "40",
                        "reorder_quantity": "200",
                        "only_if_all_thresholds_zero": True,
                    }
                ]
            },
            format="json",
        )
        force_authenticate(
            request,
            user=None,
            token={"profile_id": 1, "user_id": 1, "owner_id": 1, "permissions": ["manage_inventory_item_settings"]},
        )

        response = InventoryItemViewSet.as_view({"post": "bulk_update_controls"})(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["updated_count"], 0)
        self.assertEqual(response.data["skipped_count"], 1)
        self.assertEqual(response.data["results"][0]["skip_reason"], "Replenishment thresholds are already set.")
