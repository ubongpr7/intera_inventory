from io import StringIO
from decimal import Decimal
from unittest.mock import MagicMock, patch
import json
import tempfile

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from mainapps.inventory.models import InventoryCategory, InventoryItem, InventoryPlacement
from mainapps.projections.models import CatalogProductProjection, CatalogVariantProjection
from mainapps.stock.models import StockBalance, StockLocation
from mainapps.inventory.views import InventoryItemViewSet, get_inventory_setup_summary
from subapps.kafka.consumers.catalog import handle_catalog_variant_event
from subapps.kafka.producers.inventory import _resolve_catalog_variant
from subapps.services.location_scope import ensure_inventory_item_placement, get_workspace_default_structural_location
from subapps.services.inventory_read_model import get_inventory_item_summary_map, get_low_stock_rows
from subapps.services.inventory_variant_cleanup import delete_inventory_item_if_safe
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
        StockLocation.objects.create(
            profile_id=7,
            name="Main Warehouse",
            structural=True,
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(handle_catalog_variant_event(self._event()))

        variant = CatalogVariantProjection.objects.get()
        item = InventoryItem.objects.get(profile_id=7, product_variant_id=variant.variant_id)
        placement = InventoryPlacement.objects.get(inventory_item=item)

        self.assertEqual(item.product_template_id, variant.product_id)
        self.assertEqual(item.name_snapshot, "Phone - Black")
        self.assertEqual(item.sku_snapshot, "SKU-111")
        self.assertEqual(item.barcode_snapshot, "BAR-111")
        self.assertEqual(item.product_variant_image_url, "https://example.com/phone.jpg")
        self.assertEqual(item.inventory_type, "finished_good")
        self.assertTrue(item.track_stock)
        self.assertEqual(item.status, "active")
        self.assertEqual(item.metadata["source"], "catalog_variant_projection")
        self.assertTrue(placement.structural_location.structural)
        self.assertEqual(placement.structural_location.profile_id, 7)
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

    @patch("subapps.kafka.consumers.catalog.publish_inventory_availability_upserted")
    def test_catalog_variant_deleted_event_removes_zero_stock_inventory_item(self, publish_availability):
        stock_location = StockLocation.objects.create(
            profile_id=7,
            name="Main Warehouse",
            structural=True,
        )
        with self.captureOnCommitCallbacks(execute=True):
            handle_catalog_variant_event(self._event())

        item = InventoryItem.objects.get(profile_id=7, product_variant_id="11111111-1111-1111-1111-111111111111")
        StockBalance.objects.create(
            profile_id=7,
            inventory_item=item,
            stock_location=stock_location,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("0"),
        )

        self.assertTrue(handle_catalog_variant_event(self._event(event_name="catalog.variant.deleted")))
        self.assertFalse(InventoryItem.objects.filter(id=item.id).exists())
        publish_availability.assert_called_once()

    @patch("subapps.kafka.consumers.catalog.publish_inventory_availability_upserted")
    def test_catalog_variant_deleted_event_keeps_inventory_item_with_non_zero_stock(self, publish_availability):
        stock_location = StockLocation.objects.create(
            profile_id=7,
            name="Main Warehouse",
            structural=True,
        )
        with self.captureOnCommitCallbacks(execute=True):
            handle_catalog_variant_event(self._event())

        item = InventoryItem.objects.get(profile_id=7, product_variant_id="11111111-1111-1111-1111-111111111111")
        StockBalance.objects.create(
            profile_id=7,
            inventory_item=item,
            stock_location=stock_location,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("1"),
            quantity_available=Decimal("4"),
        )

        self.assertTrue(handle_catalog_variant_event(self._event(event_name="catalog.variant.deleted")))
        self.assertTrue(InventoryItem.objects.filter(id=item.id).exists())
        publish_availability.assert_called_once()

    def test_delete_inventory_item_if_safe_keeps_item_with_protected_relations(self):
        stock_location = StockLocation.objects.create(
            profile_id=7,
            name="Main Warehouse",
            structural=True,
        )
        with self.captureOnCommitCallbacks(execute=True):
            handle_catalog_variant_event(self._event())

        item = InventoryItem.objects.get(profile_id=7, product_variant_id="11111111-1111-1111-1111-111111111111")
        StockBalance.objects.create(
            profile_id=7,
            inventory_item=item,
            stock_location=stock_location,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("0"),
        )

        with patch(
            "subapps.services.inventory_variant_cleanup.get_inventory_item_delete_blockers",
            return_value={"purchase_order_lines": 1},
        ):
            outcome = delete_inventory_item_if_safe(item)

        self.assertFalse(outcome.deleted)
        self.assertEqual(outcome.blocked_relations["purchase_order_lines"], 1)
        self.assertTrue(InventoryItem.objects.filter(id=item.id).exists())

    def test_cleanup_orphan_inventory_items_command_deletes_zero_stock_orphans(self):
        stock_location = StockLocation.objects.create(
            profile_id=7,
            name="Main Warehouse",
            structural=True,
        )
        with self.captureOnCommitCallbacks(execute=True):
            handle_catalog_variant_event(self._event())

        item = InventoryItem.objects.get(profile_id=7, product_variant_id="11111111-1111-1111-1111-111111111111")
        StockBalance.objects.create(
            profile_id=7,
            inventory_item=item,
            stock_location=stock_location,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("0"),
        )

        variant_file = tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False)
        variant_file.write(json.dumps([]))
        variant_file.flush()
        variant_file.close()

        out = StringIO()
        call_command(
            "cleanup_orphan_inventory_items",
            profile_id=7,
            valid_variant_ids_file=variant_file.name,
            apply=True,
            stdout=out,
        )

        self.assertFalse(InventoryItem.objects.filter(id=item.id).exists())
        self.assertIn("deleted=1", out.getvalue())

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


class InventoryPlacementRepairCommandTests(TestCase):
    def test_repair_inventory_placements_creates_missing_workspace_placement(self):
        structural_root = StockLocation.objects.create(
            profile_id=1,
            name="Main Warehouse",
            structural=True,
        )
        item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Copper Wire",
        )

        output = StringIO()
        call_command("repair_inventory_placements", profile_id=1, apply=True, stdout=output)

        placement = InventoryPlacement.objects.get(inventory_item=item)
        self.assertEqual(placement.structural_location_id, structural_root.id)
        self.assertTrue(placement.active)
        self.assertIn("Applied 1 inventory placement repair(s).", output.getvalue())


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
        stock_analytics.assert_called_once_with(
            profile_id=9,
            stock_location=None,
            stock_locations=None,
        )
        low_stock_rows.assert_called_once_with(
            inventory_queryset,
            stock_location=None,
            stock_locations=None,
        )


class InventoryPlacementScopeTests(TestCase):
    def setUp(self):
        self.structural_root = StockLocation.objects.create(
            profile_id=1,
            name="Main Warehouse",
            structural=True,
        )
        self.non_structural_child = StockLocation.objects.create(
            profile_id=1,
            name="Shelf A1",
            structural=False,
            parent=self.structural_root,
        )
        self.secondary_structural_root = StockLocation.objects.create(
            profile_id=1,
            name="Airport Store",
            structural=True,
        )
        self.secondary_child = StockLocation.objects.create(
            profile_id=1,
            name="Shelf B1",
            structural=False,
            parent=self.secondary_structural_root,
        )

    def test_workspace_default_structural_location_prefers_structural_root(self):
        resolved = get_workspace_default_structural_location(profile_id=1)
        self.assertEqual(resolved.id, self.structural_root.id)

    def test_ensure_inventory_item_placement_uses_category_default_location_structural_ancestor(self):
        category = InventoryCategory.objects.create(
            profile_id=1,
            name="Perfumes",
            default_location=self.non_structural_child,
        )
        item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="9PM Perfume",
            inventory_category=category,
        )

        result = ensure_inventory_item_placement(item)

        self.assertIsNotNone(result.placement)
        self.assertTrue(result.created)
        self.assertEqual(result.structural_location.id, self.structural_root.id)
        self.assertEqual(result.placement.structural_location_id, self.structural_root.id)
        self.assertEqual(result.placement.location_name_snapshot, "Main Warehouse")

    def test_ensure_inventory_item_placement_is_idempotent(self):
        item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Night Spray",
        )

        first = ensure_inventory_item_placement(item)
        second = ensure_inventory_item_placement(item)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(
            InventoryPlacement.objects.filter(inventory_item=item, structural_location=self.structural_root).count(),
            1,
        )

    def test_inventory_summary_can_scope_to_structural_location_descendants(self):
        item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Night Spray",
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=item,
            stock_location=self.non_structural_child,
            quantity_on_hand=Decimal("9"),
            quantity_reserved=Decimal("1"),
            quantity_available=Decimal("8"),
        )

        summary_map = get_inventory_item_summary_map([item], stock_location=self.structural_root)

        self.assertEqual(summary_map[item.id]["quantity"], Decimal("9"))
        self.assertEqual(summary_map[item.id]["quantity_available"], Decimal("8"))
        self.assertEqual(summary_map[item.id]["location_name"], "Main Warehouse")
        self.assertEqual(summary_map[item.id]["location_breakdown"][0]["structural_location_name"], "Main Warehouse")

    def test_inventory_summary_groups_leaf_locations_under_one_structural_store(self):
        item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Cocoa Butter",
        )
        second_child = StockLocation.objects.create(
            profile_id=1,
            name="Shelf A2",
            structural=False,
            parent=self.structural_root,
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=item,
            stock_location=self.non_structural_child,
            quantity_on_hand=Decimal("4"),
            quantity_reserved=Decimal("1"),
            quantity_available=Decimal("3"),
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=item,
            stock_location=second_child,
            quantity_on_hand=Decimal("6"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("6"),
        )

        summary = get_inventory_item_summary_map([item])[item.id]

        self.assertEqual(summary["location_count"], 1)
        self.assertEqual(summary["location_name"], "Main Warehouse")
        self.assertEqual(summary["location_breakdown"][0]["quantity"], Decimal("10"))
        self.assertEqual(summary["location_breakdown"][0]["leaf_location_count"], 2)

    def test_inventory_summary_can_scope_to_multiple_structural_locations(self):
        item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Nivea Lotion",
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=item,
            stock_location=self.non_structural_child,
            quantity_on_hand=Decimal("4"),
            quantity_reserved=Decimal("1"),
            quantity_available=Decimal("3"),
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=item,
            stock_location=self.secondary_child,
            quantity_on_hand=Decimal("6"),
            quantity_reserved=Decimal("2"),
            quantity_available=Decimal("4"),
        )

        summary = get_inventory_item_summary_map(
            [item],
            stock_locations=[self.structural_root, self.secondary_structural_root],
        )[item.id]

        self.assertEqual(summary["quantity"], Decimal("10"))
        self.assertEqual(summary["quantity_reserved"], Decimal("3"))
        self.assertEqual(summary["quantity_available"], Decimal("7"))
        self.assertEqual(summary["location_count"], 2)
        self.assertEqual(
            {entry["structural_location_name"] for entry in summary["location_breakdown"]},
            {"Main Warehouse", "Airport Store"},
        )

    def test_inventory_setup_summary_can_scope_to_multiple_structural_locations(self):
        item_a = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Nivea Lotion",
            minimum_stock_level=Decimal("5"),
        )
        item_b = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Body Spray",
            minimum_stock_level=Decimal("5"),
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=item_a,
            stock_location=self.non_structural_child,
            quantity_on_hand=Decimal("4"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("4"),
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=item_b,
            stock_location=self.secondary_child,
            quantity_on_hand=Decimal("8"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("8"),
        )

        summary = get_inventory_setup_summary(
            profile_id=1,
            stock_locations=[self.structural_root],
        )

        self.assertEqual(summary["total_inventory_items"], 1)
        self.assertEqual(summary["low_stock_count"], 1)
        self.assertEqual(summary["total_stock_value"], Decimal("0"))


class InventoryReorderQueueViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.location = StockLocation.objects.create(
            profile_id=1,
            name="Main Warehouse",
            structural=True,
        )
        self.needs_reorder_item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Needs Reorder",
            reorder_point=Decimal("40"),
            reorder_quantity=Decimal("120"),
            minimum_stock_level=Decimal("10"),
        )
        self.healthy_item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Healthy Stock",
            reorder_point=Decimal("40"),
            reorder_quantity=Decimal("120"),
            minimum_stock_level=Decimal("10"),
        )
        self.zero_reorder_threshold_item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Zero Reorder Threshold",
            reorder_point=Decimal("0"),
            reorder_quantity=Decimal("120"),
            minimum_stock_level=Decimal("10"),
        )
        self.out_of_stock_item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Out Of Stock",
            reorder_point=Decimal("40"),
            reorder_quantity=Decimal("120"),
            minimum_stock_level=Decimal("10"),
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=self.needs_reorder_item,
            stock_location=self.location,
            quantity_on_hand=Decimal("20"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("20"),
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=self.healthy_item,
            stock_location=self.location,
            quantity_on_hand=Decimal("3000"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("3000"),
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=self.zero_reorder_threshold_item,
            stock_location=self.location,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("0"),
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=self.out_of_stock_item,
            stock_location=self.location,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("0"),
        )

    def _authenticated_request(self, path):
        request = self.factory.get(path)
        force_authenticate(
            request,
            user=type("User", (), {"id": 1, "is_authenticated": True})(),
            token={
                "profile_id": "1",
                "user_id": "1",
                "owner_id": "1",
                "permissions": ["read_inventory_item"],
            },
        )
        return request

    def test_needs_reorder_action_returns_only_items_below_positive_reorder_point(self):
        request = self._authenticated_request("/inventory_api/items/needs_reorder/")

        response = InventoryItemViewSet.as_view({"get": "needs_reorder"})(request)

        self.assertEqual(response.status_code, 200)
        returned_ids = {str(item["id"]) for item in response.data}
        self.assertIn(str(self.needs_reorder_item.id), returned_ids)
        self.assertNotIn(str(self.healthy_item.id), returned_ids)
        self.assertNotIn(str(self.zero_reorder_threshold_item.id), returned_ids)

    def test_low_stock_action_excludes_zero_stock_and_zero_threshold_items(self):
        request = self._authenticated_request("/inventory_api/items/low_stock/")

        response = InventoryItemViewSet.as_view({"get": "low_stock"})(request)

        self.assertEqual(response.status_code, 200)
        returned_ids = {str(item["id"]) for item in response.data}
        self.assertNotIn(str(self.out_of_stock_item.id), returned_ids)
        self.assertNotIn(str(self.zero_reorder_threshold_item.id), returned_ids)


class InventoryAdjustStockScopeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.structural_root = StockLocation.objects.create(
            profile_id=1,
            name="Main Warehouse",
            structural=True,
        )
        self.structural_child = StockLocation.objects.create(
            profile_id=1,
            name="Shelf A1",
            structural=False,
            parent=self.structural_root,
        )
        self.secondary_root = StockLocation.objects.create(
            profile_id=1,
            name="Airport Store",
            structural=True,
        )
        self.secondary_child = StockLocation.objects.create(
            profile_id=1,
            name="Shelf B1",
            structural=False,
            parent=self.secondary_root,
        )
        self.inventory_item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Scoped Lotion",
        )

    def _authenticate(self, request):
        force_authenticate(
            request,
            user=None,
            token={"profile_id": 1, "user_id": 1, "owner_id": 1, "permissions": ["manage_inventory_item_settings"]},
        )

    @patch("mainapps.inventory.views.StockDomainService.adjust_stock")
    def test_adjust_stock_rejects_location_outside_selected_structural_scope(self, adjust_stock):
        request = self.factory.post(
            f"/inventory/items/{self.inventory_item.id}/adjust_stock/",
            {
                "location_id": str(self.secondary_child.id),
                "structural_location_id": str(self.structural_root.id),
                "quantity_change": "2",
                "reason": "Manual correction",
            },
            format="json",
        )
        self._authenticate(request)

        response = InventoryItemViewSet.as_view({"post": "adjust_stock"})(request, pk=self.inventory_item.id)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "Stock location is outside the selected structural location scope")
        adjust_stock.assert_not_called()

    @patch("mainapps.inventory.views.StockDomainService.adjust_stock")
    def test_adjust_stock_accepts_location_inside_selected_structural_scope(self, adjust_stock):
        adjust_stock.return_value = {
            "old_quantity": Decimal("4"),
            "new_quantity": Decimal("6"),
        }
        request = self.factory.post(
            f"/inventory/items/{self.inventory_item.id}/adjust_stock/",
            {
                "location_id": str(self.structural_child.id),
                "structural_location_id": str(self.structural_root.id),
                "quantity_change": "2",
                "reason": "Manual correction",
            },
            format="json",
        )
        self._authenticate(request)

        response = InventoryItemViewSet.as_view({"post": "adjust_stock"})(request, pk=self.inventory_item.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["new_quantity"], Decimal("6"))
        adjust_stock.assert_called_once()

    def test_adjust_stock_rejects_unknown_structural_scope(self):
        request = self.factory.post(
            f"/inventory/items/{self.inventory_item.id}/adjust_stock/",
            {
                "location_id": str(self.structural_child.id),
                "structural_location_id": "00000000-0000-0000-0000-000000000000",
                "quantity_change": "2",
                "reason": "Manual correction",
            },
            format="json",
        )
        self._authenticate(request)

        response = InventoryItemViewSet.as_view({"post": "adjust_stock"})(request, pk=self.inventory_item.id)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "The selected structural location scope is unavailable")


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
