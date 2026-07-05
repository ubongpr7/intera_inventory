import json
import uuid
from decimal import Decimal
from io import StringIO
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from mainapps.inventory.models import InventoryItem, InventoryPlacement
from mainapps.stock.models import StockBalance, StockLocation, StockLocationType, StockReservation, StockReservationStatus
from mainapps.stock.serializers import InventoryItemDetailSerializer
from mainapps.stock.views import (
    StockReservationViewSet,
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


class InventoryItemDetailSerializerContractTests(SimpleTestCase):
    def test_business_fields_are_writable_for_partial_updates(self):
        serializer = InventoryItemDetailSerializer(
            data={
                "name_snapshot": "Finished Soap",
                "inventory_type": "finished_good",
                "status": "active",
                "reorder_point": "12.00000",
                "track_lot": True,
            },
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["name_snapshot"], "Finished Soap")
        self.assertEqual(serializer.validated_data["inventory_type"], "finished_good")

    def test_identity_and_computed_fields_remain_read_only(self):
        serializer = InventoryItemDetailSerializer()

        for field_name in (
            "created_by_user_id",
            "updated_by_user_id",
            "created_by_details",
            "updated_by_details",
            "quantity",
            "stock_status",
        ):
            self.assertTrue(serializer.fields[field_name].read_only, field_name)


class StockViewFilterTests(SimpleTestCase):
    def test_filter_inventory_items_for_location_uses_location_scope(self):
        queryset = MagicMock()
        filtered_queryset = MagicMock()
        location_queryset = MagicMock()
        location_queryset.first.return_value = MagicMock(profile_id=7)

        with patch("mainapps.stock.views.StockLocation.objects.filter", return_value=location_queryset):
            with patch("mainapps.stock.views._apply_location_scope_filter", return_value=filtered_queryset) as apply_scope:
                filter_inventory_items_for_location(queryset, "location-id")

        apply_scope.assert_called_once_with(
            queryset,
            field_name="stock_balances__stock_location_id",
            profile_id=7,
            location_id="location-id",
        )
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
        structural_location = SimpleNamespace(id="struct-1", name="Airport Road, Oshodi Store")

        balance_queryset = MagicMock()
        balance_queryset.select_related.return_value.order_by.return_value = [balance]

        movement_queryset = MagicMock()
        movement_queryset.values.return_value.annotate.return_value = []

        serial_queryset = MagicMock()
        serial_queryset.values.return_value.annotate.return_value = []

        with patch("subapps.services.inventory_read_model.StockBalance.objects.filter", return_value=balance_queryset):
            with patch("subapps.services.inventory_read_model.StockMovement.objects.filter", return_value=movement_queryset):
                with patch("subapps.services.inventory_read_model.StockSerial.objects.filter", return_value=serial_queryset):
                    with patch("subapps.services.inventory_read_model.resolve_structural_location", return_value=structural_location):
                        summary = get_inventory_item_summary_map([inventory_item])[inventory_item.id]

        self.assertEqual(summary["quantity"], Decimal("5"))
        self.assertEqual(summary["quantity_reserved"], Decimal("1"))
        self.assertEqual(summary["quantity_available"], Decimal("4"))
        self.assertEqual(summary["location_name"], "Airport Road, Oshodi Store")
        self.assertEqual(summary["location_breakdown"][0]["structural_location_name"], "Airport Road, Oshodi Store")
        self.assertEqual(summary["location_breakdown"][0]["leaf_locations"][0]["stock_location_name"], "Main Warehouse")

    def test_location_stock_summary_reads_from_stock_balances_only(self):
        location = MagicMock()
        location.id = "loc-1"
        location.profile_id = 1
        balances = MagicMock()
        balances.select_related.return_value = balances
        balances.__iter__.return_value = iter([])
        balances.filter.return_value.count.return_value = 0

        with patch("subapps.services.inventory_read_model.get_location_scope_ids", return_value=["loc-1"]):
            with patch("subapps.services.inventory_read_model.StockBalance.objects.filter", return_value=balances):
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

    def test_adjust_stock_positive_ensures_inventory_item_placement(self):
        stock_location = MagicMock()
        balance = MagicMock(quantity_on_hand=Decimal("2"))

        with patch(
            "subapps.services.stock_domain.StockDomainService._get_locked_balance",
            return_value=balance,
        ):
            with patch("subapps.services.stock_domain.StockMovement.objects.create"):
                with patch("subapps.services.stock_domain.StockDomainService._publish_inventory_availability_on_commit"):
                    with patch("subapps.services.stock_domain.ensure_inventory_item_placement") as ensure_placement:
                        StockDomainService.adjust_stock.__wrapped__(
                            StockDomainService,
                            inventory_item=self.inventory_item,
                            stock_location=stock_location,
                            quantity_change=Decimal("3"),
                            actor_user_id=11,
                            reason="Top-up",
                        )

        ensure_placement.assert_called_once_with(
            self.inventory_item,
            stock_location=stock_location,
            created_by_user_id=11,
            updated_by_user_id=11,
        )

    def test_operational_stock_location_rejects_structural_root(self):
        structural_location = SimpleNamespace(structural=True)

        with self.assertRaises(StockDomainError):
            StockDomainService._ensure_operational_stock_location(structural_location, label="Receiving location")

    def test_operational_stock_location_accepts_leaf_location(self):
        leaf_location = SimpleNamespace(structural=False)

        resolved = StockDomainService._ensure_operational_stock_location(leaf_location)

        self.assertIs(resolved, leaf_location)

    def test_receive_purchase_line_ensures_inventory_item_placement(self):
        purchase_order = MagicMock(reference="PO-001", supplier=MagicMock())
        line_item = MagicMock(
            quantity_received=Decimal("0"),
            quantity=Decimal("5"),
            remaining_quantity=Decimal("5"),
            unit_price=Decimal("120"),
            batch_number="",
            manufactured_date=None,
            expiry_date=None,
        )
        stock_location = MagicMock()
        goods_receipt = MagicMock()
        goods_receipt_line = MagicMock(id=uuid.uuid4(), lot_number="")
        inventory_item = MagicMock(id=uuid.uuid4(), profile_id=1, track_serial=False, track_lot=False)
        balance = MagicMock(quantity_on_hand=Decimal("0"))

        with patch("subapps.services.stock_domain._validate_inventory_dates"):
            with patch("subapps.services.stock_domain.GoodsReceiptLine.objects.create", return_value=goods_receipt_line):
                with patch("subapps.services.stock_domain.StockMovement.objects.create"):
                    with patch("subapps.services.stock_domain.StockDomainService._resolve_profile_id", return_value=1):
                        with patch("subapps.services.stock_domain.StockDomainService.ensure_inventory_item", return_value=inventory_item):
                            with patch("subapps.services.stock_domain.StockDomainService._get_locked_balance", return_value=balance):
                                with patch("subapps.services.stock_domain.StockDomainService._create_receipt_serials", return_value=[]):
                                    with patch("subapps.services.stock_domain.StockDomainService._publish_inventory_availability_on_commit"):
                                        with patch("subapps.services.stock_domain.StockDomainService._publish_inventory_purchase_price_on_commit"):
                                            with patch("subapps.services.stock_domain.ensure_inventory_item_placement") as ensure_placement:
                                                StockDomainService.receive_purchase_line.__wrapped__(
                                                    StockDomainService,
                                                    purchase_order=purchase_order,
                                                    line_item=line_item,
                                                    stock_location=stock_location,
                                                    quantity_received=Decimal("2"),
                                                    actor_user_id=13,
                                                    goods_receipt=goods_receipt,
                                                )

        ensure_placement.assert_called_once_with(
            inventory_item,
            stock_location=stock_location,
            created_by_user_id=13,
            updated_by_user_id=13,
        )

    def test_receive_purchase_line_uses_explicit_unit_cost_for_receipt_records(self):
        purchase_order = MagicMock(reference="PO-002", supplier=MagicMock(), order_currency="NGN")
        line_item = MagicMock(
            quantity_received=Decimal("0"),
            quantity=Decimal("5"),
            remaining_quantity=Decimal("5"),
            unit_price=Decimal("120"),
            batch_number="LOT-1",
            manufactured_date=None,
            expiry_date=None,
        )
        stock_location = MagicMock()
        goods_receipt = MagicMock()
        goods_receipt_line = MagicMock(id=uuid.uuid4(), lot_number="")
        inventory_item = MagicMock(id=uuid.uuid4(), profile_id=1, track_serial=False, track_lot=True)
        balance = MagicMock(quantity_on_hand=Decimal("0"))

        with patch("subapps.services.stock_domain._validate_inventory_dates"):
            with patch("subapps.services.stock_domain.GoodsReceiptLine.objects.create", return_value=goods_receipt_line) as create_receipt_line:
                with patch("subapps.services.stock_domain.StockLot.objects.create") as create_stock_lot:
                    with patch("subapps.services.stock_domain.StockMovement.objects.create") as create_stock_movement:
                        with patch("subapps.services.stock_domain.StockDomainService._resolve_profile_id", return_value=1):
                            with patch("subapps.services.stock_domain.StockDomainService.ensure_inventory_item", return_value=inventory_item):
                                with patch("subapps.services.stock_domain.StockDomainService._get_locked_balance", return_value=balance):
                                    with patch("subapps.services.stock_domain.StockDomainService._create_receipt_serials", return_value=[]):
                                        with patch("subapps.services.stock_domain.StockDomainService._publish_inventory_availability_on_commit"):
                                            with patch("subapps.services.stock_domain.StockDomainService._publish_inventory_purchase_price_on_commit"):
                                                with patch("subapps.services.stock_domain.ensure_inventory_item_placement"):
                                                    StockDomainService.receive_purchase_line.__wrapped__(
                                                        StockDomainService,
                                                        purchase_order=purchase_order,
                                                        line_item=line_item,
                                                        stock_location=stock_location,
                                                        quantity_received=Decimal("2"),
                                                        unit_cost=Decimal("155.75"),
                                                        actor_user_id=13,
                                                        goods_receipt=goods_receipt,
                                                    )

        self.assertEqual(create_receipt_line.call_args.kwargs["unit_cost"], Decimal("155.75"))
        self.assertEqual(create_stock_lot.call_args.kwargs["unit_cost"], Decimal("155.75"))
        self.assertEqual(create_stock_movement.call_args.kwargs["unit_cost"], Decimal("155.75"))


class StockLocationRepairCommandTests(TestCase):
    def setUp(self):
        self.warehouse_type = StockLocationType.objects.create(name="Warehouse", description="Large storage facility")
        self.showroom_type = StockLocationType.objects.create(name="Showroom", description="Customer-facing display area")
        self.backroom_type = StockLocationType.objects.create(name="Backroom", description="Staff-only storage")
        self.returns_type = StockLocationType.objects.create(name="Returns Area", description="Return handling")

    def test_repair_stock_location_defaults_assigns_type_parent_and_code(self):
        root = StockLocation.objects.create(
            profile_id=1,
            name="Main Fashion Warehouse",
            structural=True,
        )
        child = StockLocation.objects.create(
            profile_id=1,
            name="Retail Floor",
            structural=False,
        )
        returns = StockLocation.objects.create(
            profile_id=1,
            name="Returns Rack",
            structural=False,
        )

        output = StringIO()
        call_command("repair_stock_location_defaults", profile_id=1, apply=True, stdout=output)

        root.refresh_from_db()
        child.refresh_from_db()
        returns.refresh_from_db()

        self.assertEqual(root.location_type, self.warehouse_type)
        self.assertTrue(root.code)
        self.assertEqual(child.parent_id, root.id)
        self.assertEqual(child.location_type, self.showroom_type)
        self.assertTrue(child.code)
        self.assertEqual(returns.parent_id, root.id)
        self.assertEqual(returns.location_type, self.returns_type)
        self.assertIn("Applied 3 stock location repair(s).", output.getvalue())

    def test_export_location_scope_map_includes_structural_resolution_for_children(self):
        root = StockLocation.objects.create(
            profile_id=1,
            name="Main Warehouse",
            structural=True,
            location_type=self.warehouse_type,
        )
        child = StockLocation.objects.create(
            profile_id=1,
            name="Retail Floor",
            structural=False,
            parent=root,
            location_type=self.showroom_type,
        )

        with NamedTemporaryFile("w+", suffix=".json") as handle:
            call_command(
                "export_location_scope_map",
                profile_id=1,
                output=handle.name,
                pretty=True,
            )
            handle.seek(0)
            payload = json.load(handle)

        self.assertEqual(payload["profile_id"], 1)
        self.assertEqual(payload["default_structural_location_id"], str(root.id))
        retail_floor_entries = payload["location_name_map"]["retail floor"]
        self.assertEqual(retail_floor_entries[0]["location_id"], str(child.id))
        self.assertEqual(retail_floor_entries[0]["structural_location_id"], str(root.id))


class StoreTopologySeedCommandTests(TestCase):
    def setUp(self):
        self.root_type = StockLocationType.objects.create(name="Warehouse", description="Large storage facility")
        self.root = StockLocation.objects.create(
            profile_id=1,
            name="Gberigbe Store",
            structural=True,
            location_type=self.root_type,
        )
        self.item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Gold bottle and black cap, 7ml edition, Perfume - Variant 1",
            inventory_type="finished_good",
            barcode_snapshot="310709297930",
            status="active",
        )
        InventoryPlacement.objects.create(
            profile_id=1,
            inventory_item=self.item,
            structural_location=self.root,
            active=True,
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=self.item,
            stock_location=self.root,
            quantity_on_hand=Decimal("100.00000"),
            quantity_reserved=Decimal("0.00000"),
        )

    def test_seed_profile_store_topology_creates_repeated_sections_and_moves_root_stock(self):
        output = StringIO()

        call_command("seed_profile_store_topology", profile_id=1, apply=True, stdout=output)

        roots = list(
            StockLocation.objects.filter(profile_id=1, structural=True, parent__isnull=True)
            .order_by("name")
            .values_list("name", flat=True)
        )
        self.assertEqual(
            roots,
            ["Agric, Ikorodu Store", "Airport Road, Oshodi Store", "Gberigbe Store"],
        )

        for root_name in roots:
            root = StockLocation.objects.get(profile_id=1, name=root_name, parent__isnull=True)
            sales_floor = StockLocation.objects.get(profile_id=1, name="Sales Floor", parent=root)
            self.assertFalse(sales_floor.structural)
            self.assertEqual(sales_floor.location_type.name, "Showroom")
            fragrance = StockLocation.objects.get(profile_id=1, name="Beauty & Fragrance Gondola", parent=sales_floor)
            self.assertEqual(fragrance.location_type.name, "Gondola")

        self.assertFalse(
            StockBalance.objects.filter(profile_id=1, inventory_item=self.item, stock_location=self.root).exists()
        )

        new_balances = list(
            StockBalance.objects.filter(profile_id=1, inventory_item=self.item)
            .select_related("stock_location")
            .order_by("stock_location__parent__name")
        )
        self.assertEqual(len(new_balances), 3)
        self.assertEqual(sum(balance.quantity_on_hand for balance in new_balances), Decimal("100.00000"))
        self.assertTrue(all(balance.stock_location.parent is not None for balance in new_balances))
        self.assertTrue(
            InventoryPlacement.objects.filter(profile_id=1, inventory_item=self.item, structural_location__name="Airport Road, Oshodi Store").exists()
        )
        self.assertTrue(
            InventoryPlacement.objects.filter(profile_id=1, inventory_item=self.item, structural_location__name="Agric, Ikorodu Store").exists()
        )
        self.assertIn("Applied store topology seed for profile 1", output.getvalue())


class StockLocationDefaultStructuralLocationTests(TestCase):
    def setUp(self):
        self.location_type = StockLocationType.objects.create(name="Warehouse", description="Large storage facility")

    def test_first_structural_location_becomes_workspace_default(self):
        root = StockLocation.objects.create(
            profile_id=11,
            name="Gberigbe Store",
            structural=True,
            location_type=self.location_type,
        )

        root.refresh_from_db()

        self.assertTrue(root.is_default_structural_location)

    def test_switching_default_structural_location_clears_previous_default(self):
        first = StockLocation.objects.create(
            profile_id=12,
            name="Gberigbe Store",
            structural=True,
            location_type=self.location_type,
        )
        second = StockLocation.objects.create(
            profile_id=12,
            name="Airport Road, Oshodi Store",
            structural=True,
            location_type=self.location_type,
        )

        second.is_default_structural_location = True
        second.save()

        first.refresh_from_db()
        second.refresh_from_db()

        self.assertFalse(first.is_default_structural_location)
        self.assertTrue(second.is_default_structural_location)

    def test_deleting_default_structural_location_promotes_another_structural_location(self):
        first = StockLocation.objects.create(
            profile_id=13,
            name="Gberigbe Store",
            structural=True,
            location_type=self.location_type,
        )
        second = StockLocation.objects.create(
            profile_id=13,
            name="Agric, Ikorodu Store",
            structural=True,
            location_type=self.location_type,
        )

        first.delete()
        second.refresh_from_db()

        self.assertTrue(second.is_default_structural_location)

    def test_non_structural_locations_cannot_become_workspace_default(self):
        with self.assertRaises(ValidationError):
            StockLocation.objects.create(
                profile_id=14,
                name="Sales Floor",
                structural=False,
                is_default_structural_location=True,
                location_type=self.location_type,
            )


class StockAnalyticsScopeTests(TestCase):
    def setUp(self):
        self.primary_root = StockLocation.objects.create(
            profile_id=1,
            name="Primary Store",
            structural=True,
        )
        self.primary_leaf = StockLocation.objects.create(
            profile_id=1,
            name="Primary Shelf",
            structural=False,
            parent=self.primary_root,
        )
        self.secondary_root = StockLocation.objects.create(
            profile_id=1,
            name="Secondary Store",
            structural=True,
        )
        self.secondary_leaf = StockLocation.objects.create(
            profile_id=1,
            name="Secondary Shelf",
            structural=False,
            parent=self.secondary_root,
        )
        self.item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Body Spray",
            inventory_type="finished_good",
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=self.item,
            stock_location=self.primary_leaf,
            quantity_on_hand=Decimal("5"),
            quantity_reserved=Decimal("1"),
            quantity_available=Decimal("4"),
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=self.item,
            stock_location=self.secondary_leaf,
            quantity_on_hand=Decimal("8"),
            quantity_reserved=Decimal("2"),
            quantity_available=Decimal("6"),
        )

    def test_profile_stock_analytics_can_scope_to_multiple_structural_locations(self):
        analytics = get_profile_stock_analytics(
            profile_id=1,
            stock_locations=[self.primary_root, self.secondary_root],
        )

        self.assertEqual(analytics["total_inventory_items"], 1)
        self.assertEqual(analytics["total_locations"], 2)
        self.assertEqual(
            {entry["location_name"] for entry in analytics["location_distribution"]},
            {"Primary Store", "Secondary Store"},
        )

    def test_profile_stock_analytics_can_scope_to_one_structural_location(self):
        analytics = get_profile_stock_analytics(
            profile_id=1,
            stock_location=self.primary_root,
        )

        self.assertEqual(analytics["total_inventory_items"], 1)
        self.assertEqual(analytics["total_locations"], 1)
        self.assertEqual(len(analytics["location_distribution"]), 1)
        self.assertEqual(analytics["location_distribution"][0]["location_name"], "Primary Store")
        self.assertEqual(analytics["location_distribution"][0]["total_quantity"], Decimal("5"))


class LocationScopeAuditCommandTests(TestCase):
    def test_audit_location_scope_reports_missing_placements_and_can_fail(self):
        root = StockLocation.objects.create(
            profile_id=1,
            name="Audit Root",
            structural=True,
        )
        leaf = StockLocation.objects.create(
            profile_id=1,
            name="Audit Shelf",
            structural=False,
            parent=root,
        )
        item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Audit Item",
        )
        StockBalance.objects.create(
            profile_id=1,
            inventory_item=item,
            stock_location=leaf,
            quantity_on_hand=Decimal("3"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("3"),
        )

        output = StringIO()
        call_command("audit_location_scope", profile_id=1, pretty=True, stdout=output)
        payload = json.loads(output.getvalue())

        self.assertEqual(payload["default_structural_location_name"], "Audit Root")
        self.assertEqual(payload["issues"]["inventory_items_without_active_placement_count"], 1)
        self.assertEqual(payload["issues"]["balances_without_resolved_structural_scope_count"], 0)

        with self.assertRaises(CommandError):
            call_command("audit_location_scope", profile_id=1, fail_on_issues=True, stdout=StringIO())


class StockReservationAuditEventTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.stock_location = StockLocation.objects.create(
            profile_id=1,
            name="Main Warehouse Bin A",
            structural=False,
        )
        self.inventory_item = InventoryItem.objects.create(
            profile_id=1,
            name_snapshot="Blue Detergent",
            sku_snapshot="SKU-BLUE-500",
            barcode_snapshot="BAR-BLUE-500",
            product_variant_image_url="https://cdn.example.com/blue-detergent.png",
        )
        self.reservation = StockReservation.objects.create(
            profile_id=1,
            inventory_item=self.inventory_item,
            stock_location=self.stock_location,
            external_order_type="manual_hold",
            external_order_id="HOLD-1",
            reserved_quantity=Decimal("4"),
            fulfilled_quantity=Decimal("0"),
            status=StockReservationStatus.ACTIVE,
        )

    def _authenticate(self, request):
        force_authenticate(
            request,
            user=None,
            token={"profile_id": 1, "user_id": 1, "owner_id": 1, "permissions": ["update_inventory_item", "read_inventory_item"]},
        )

    @patch("mainapps.stock.views.publish_inventory_admin_event")
    @patch("mainapps.stock.views.StockDomainService.reserve_stock")
    def test_create_emits_audit_event_for_stock_reservation(self, reserve_stock, publish_inventory_admin_event):
        reserve_stock.return_value = {"reservation": self.reservation}
        request = self.factory.post(
            "/stock/reservations/",
            {
                "inventory_item_id": str(self.inventory_item.id),
                "location_id": str(self.stock_location.id),
                "quantity": "4",
                "external_order_type": "manual_hold",
                "external_order_id": "HOLD-1",
                "notes": "Manual hold for stock review",
            },
            format="json",
        )
        self._authenticate(request)

        response = StockReservationViewSet.as_view({"post": "create"})(request)

        self.assertEqual(response.status_code, 201)
        publish_inventory_admin_event.assert_called_once()
        payload = publish_inventory_admin_event.call_args.kwargs
        self.assertEqual(payload["event_name"], "inventory.stock_reservation.created")
        self.assertEqual(payload["payload"]["inventory_barcode"], "BAR-BLUE-500")
        self.assertEqual(payload["payload"]["display_image"], "https://cdn.example.com/blue-detergent.png")
        self.assertEqual(payload["notification_category"], "stock_alert")

    @patch("mainapps.stock.views.publish_inventory_admin_event")
    @patch("mainapps.stock.views.StockDomainService.release_reservation")
    def test_release_emits_audit_event_for_stock_reservation(self, release_reservation, publish_inventory_admin_event):
        self.reservation.fulfilled_quantity = Decimal("1")
        self.reservation.save(update_fields=["fulfilled_quantity"])
        release_reservation.return_value = {"reservation": self.reservation}
        request = self.factory.post(
            f"/stock/reservations/{self.reservation.id}/release/",
            {"quantity": "2", "notes": "Release back to floor"},
            format="json",
        )
        self._authenticate(request)

        response = StockReservationViewSet.as_view({"post": "release"})(request, pk=self.reservation.id)

        self.assertEqual(response.status_code, 200)
        payload = publish_inventory_admin_event.call_args.kwargs
        self.assertEqual(payload["event_name"], "inventory.stock_reservation.released")
        self.assertEqual(payload["before"]["reserved_quantity"], 4.0)
        self.assertEqual(payload["payload"]["inventory_sku"], "SKU-BLUE-500")

    @patch("mainapps.stock.views.publish_inventory_admin_event")
    @patch("mainapps.stock.views.StockDomainService.fulfill_reservation")
    def test_fulfill_emits_audit_event_for_stock_reservation(self, fulfill_reservation, publish_inventory_admin_event):
        self.reservation.fulfilled_quantity = Decimal("2")
        self.reservation.status = StockReservationStatus.PARTIALLY_FULFILLED
        self.reservation.save(update_fields=["fulfilled_quantity", "status"])
        fulfill_reservation.return_value = {"reservation": self.reservation}
        request = self.factory.post(
            f"/stock/reservations/{self.reservation.id}/fulfill/",
            {"quantity": "2", "notes": "Fulfilled for outbound movement"},
            format="json",
        )
        self._authenticate(request)

        response = StockReservationViewSet.as_view({"post": "fulfill"})(request, pk=self.reservation.id)

        self.assertEqual(response.status_code, 200)
        payload = publish_inventory_admin_event.call_args.kwargs
        self.assertEqual(payload["event_name"], "inventory.stock_reservation.fulfilled")
        self.assertEqual(payload["payload"]["inventory_name"], "Blue Detergent")
        self.assertEqual(payload["notification_title"], "Reservation fulfilled for Blue Detergent")
