from decimal import Decimal

from django.test import TestCase

from mainapps.inventory.models import InventoryItem
from mainapps.stock.models import StockBalance, StockLocation, StockMovement
from subapps.services.stock_domain import StockDomainService

from .models import (
    CostingPeriod,
    CostingMethod,
    MaterialRequirement,
    ProductionOperationStatus,
    ProductionOrderStatus,
    QualityInspectionStatus,
    QualityInspection,
    Recipe,
    RecipeLine,
    RecipeOperation,
    RecipeOutput,
    RecipeStatus,
    WorkCenter,
    WasteRecord,
    Nonconformance,
)
from .services import ManufacturingError, ManufacturingService


class ManufacturingWorkflowTests(TestCase):
    def setUp(self):
        self.profile_id = 901
        self.location = StockLocation.objects.create(profile_id=self.profile_id, name="Bakery")
        self.flour = self.item("Flour", "raw_material", "kg", track_lot=True)
        self.yeast = self.item("Yeast", "raw_material", "kg", track_lot=True)
        self.bread = self.item("Bread", "finished_good", "unit", track_lot=True)
        self.recipe = Recipe.objects.create(
            profile_id=self.profile_id,
            name="Bread",
            finished_good=self.bread,
            version=1,
            status=RecipeStatus.ACTIVE,
            output_quantity=Decimal("100"),
            output_uom_code="unit",
        )
        self.flour_line = RecipeLine.objects.create(
            profile_id=self.profile_id,
            recipe=self.recipe,
            component=self.flour,
            quantity=Decimal("60"),
            uom_code="kg",
        )
        RecipeLine.objects.create(
            profile_id=self.profile_id,
            recipe=self.recipe,
            component=self.yeast,
            quantity=Decimal("2"),
            uom_code="kg",
        )

    def item(self, name, inventory_type, stock_uom, *, track_lot=False):
        return InventoryItem.objects.create(
            profile_id=self.profile_id,
            name_snapshot=name,
            inventory_type=inventory_type,
            stock_uom_code=stock_uom,
            default_uom_code=stock_uom,
            track_stock=True,
            track_lot=track_lot,
            salable=inventory_type == "finished_good",
        )

    def receive(self, item, quantity, cost, lot):
        return StockDomainService.receive_stock(
            inventory_item=item,
            stock_location=self.location,
            quantity=quantity,
            quantity_uom_code=item.stock_uom_code,
            unit_cost=cost,
            lot_number=lot,
            force_lot=True,
            reference_type="test_receipt",
            reference_id=lot,
        )

    def test_production_consumes_materials_and_receives_costed_finished_lot(self):
        self.receive(self.flour, "100", "2", "FLOUR-1")
        self.receive(self.yeast, "10", "5", "YEAST-1")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-1",
        )
        ManufacturingService.release(order=order)
        issue = ManufacturingService.issue_material(
            order=order,
            recipe_line=self.flour_line,
            quantity="60",
            idempotency_key="flour-issue-1",
        )
        self.assertEqual(issue.unit_cost, Decimal("2"))
        self.assertEqual(StockMovement.objects.filter(reference_type="production_material_issue").count(), 1)
        output = ManufacturingService.complete(
            order=order,
            actual_quantity="95",
            scrap_quantity="5",
            idempotency_key="output-1",
        )
        order.refresh_from_db()
        self.assertEqual(order.status, ProductionOrderStatus.COMPLETED)
        self.assertEqual(output.quantity, Decimal("95"))
        self.assertEqual(output.unit_cost, Decimal("120") / Decimal("95"))
        balance = StockBalance.objects.get(
            profile_id=self.profile_id,
            inventory_item=self.bread,
            stock_location=self.location,
            stock_lot=output.stock_lot,
        )
        self.assertEqual(balance.quantity_on_hand, Decimal("95"))

    def test_nonconformance_is_workspace_scoped_and_tracks_quality_case(self):
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-NC-1",
        )
        case = Nonconformance.objects.create(
            profile_id=self.profile_id,
            number="NC-001",
            production_order=order,
            severity="high",
            description="Bread output failed weight check.",
        )
        self.assertEqual(Nonconformance.objects.filter(profile_id=self.profile_id, number="NC-001").count(), 1)
        self.assertFalse(Nonconformance.objects.filter(profile_id=self.profile_id + 1, number="NC-001").exists())
        case.status = "contained"
        case.containment_action = "Quarantined the affected lot."
        case.save(update_fields=["status", "containment_action", "updated_at"])
        self.assertEqual(Nonconformance.objects.get(pk=case.pk).status, "contained")

    def test_standard_costing_reports_actual_variance_and_values_output_at_standard(self):
        self.recipe.costing_method = CostingMethod.STANDARD
        self.recipe.standard_unit_cost = Decimal("3")
        self.recipe.save(update_fields=["costing_method", "standard_unit_cost"])
        self.receive(self.flour, "100", "2", "FLOUR-1")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-STANDARD",
        )
        ManufacturingService.release(order=order)
        ManufacturingService.issue_material(order=order, recipe_line=self.flour_line, quantity="60", idempotency_key="standard-input")
        summary = ManufacturingService.cost_summary(order, quantity_override="100")
        self.assertEqual(summary["actual_cost"], Decimal("120"))
        self.assertEqual(summary["standard_cost"], Decimal("300"))
        self.assertEqual(summary["variance"], Decimal("-180"))
        self.assertEqual(summary["total_cost"], Decimal("300"))
        output = ManufacturingService.complete(order=order, actual_quantity="100", idempotency_key="standard-output")
        self.assertEqual(output.unit_cost, Decimal("3"))

    def test_weighted_average_costing_uses_issue_costs_and_reports_cost_summary(self):
        self.recipe.costing_method = CostingMethod.WEIGHTED_AVERAGE
        self.recipe.save(update_fields=["costing_method"])
        first = self.receive(self.flour, "60", "2", "FLOUR-1")
        self.receive(self.flour, "60", "4", "FLOUR-2")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-WAVG",
        )
        ManufacturingService.release(order=order)
        ManufacturingService.issue_material(order=order, recipe_line=self.flour_line, quantity="60", stock_lot=first["stock_lot"], idempotency_key="weighted-input")
        summary = ManufacturingService.cost_summary(order)
        self.assertEqual(summary["material_cost"], Decimal("120"))
        self.assertEqual(summary["weighted_average_cost"], Decimal("180"))
        self.assertEqual(summary["total_cost"], Decimal("180"))

    def test_fifo_costing_is_based_on_immutable_issue_lot_cost(self):
        self.recipe.costing_method = CostingMethod.FIFO
        self.recipe.save(update_fields=["costing_method"])
        self.receive(self.flour, "60", "2", "FLOUR-1")
        self.receive(self.flour, "60", "4", "FLOUR-2")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-FIFO",
        )
        ManufacturingService.release(order=order)
        ManufacturingService.issue_material(order=order, recipe_line=self.flour_line, quantity="60", idempotency_key="fifo-input")
        summary = ManufacturingService.cost_summary(order)
        self.assertEqual(summary["costing_method"], CostingMethod.FIFO)
        self.assertEqual(summary["actual_cost"], Decimal("120"))
        self.assertEqual(summary["total_cost"], Decimal("120"))

    def test_repeated_issue_and_output_requests_are_idempotent(self):
        self.receive(self.flour, "100", "2", "FLOUR-1")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-2",
        )
        ManufacturingService.release(order=order)
        first = ManufacturingService.issue_material(order=order, recipe_line=self.flour_line, quantity="60", idempotency_key="same")
        second = ManufacturingService.issue_material(order=order, recipe_line=self.flour_line, quantity="60", idempotency_key="same")
        self.assertEqual(first.id, second.id)
        self.assertEqual(StockMovement.objects.filter(reference_type="production_material_issue").count(), 1)
        first_output = ManufacturingService.complete(order=order, actual_quantity="100", idempotency_key="output-same")
        second_output = ManufacturingService.complete(order=order, actual_quantity="100", idempotency_key="output-same")
        self.assertEqual(first_output.id, second_output.id)
        self.assertEqual(order.outputs.count(), 1)

    def test_routed_operations_add_labor_overhead_and_failed_quality_quarantines_output(self):
        work_center = WorkCenter.objects.create(
            profile_id=self.profile_id,
            code="OVEN",
            name="Oven",
            hourly_labor_rate=Decimal("600"),
            hourly_overhead_rate=Decimal("300"),
        )
        RecipeOperation.objects.create(
            profile_id=self.profile_id,
            recipe=self.recipe,
            sequence=1,
            name="Bake",
            work_center=work_center,
            expected_minutes=30,
        )
        self.receive(self.flour, "100", "2", "FLOUR-1")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-3",
        )
        ManufacturingService.release(order=order)
        operation = order.operations.get()
        self.assertEqual(operation.status, ProductionOperationStatus.PENDING)
        ManufacturingService.start_operation(operation=operation)
        ManufacturingService.complete_operation(operation=operation, actual_minutes=30)
        self.assertEqual(order.cost_entries.count(), 2)
        self.assertEqual(sum(entry.amount for entry in order.cost_entries.all()), Decimal("450"))
        output = ManufacturingService.complete(order=order, actual_quantity="100", idempotency_key="output-quality")
        inspection = ManufacturingService.create_quality_inspection(order=order, output=output, inspected_quantity="100")
        ManufacturingService.resolve_quality_inspection(
            inspection=inspection,
            status=QualityInspectionStatus.FAILED,
            inspector_user_id=42,
            disposition=QualityInspection.Disposition.SCRAP,
            quarantined_quantity="100",
        )
        inspection.refresh_from_db()
        self.assertEqual(inspection.disposition, QualityInspection.Disposition.SCRAP)
        output.stock_lot.refresh_from_db()
        self.assertEqual(output.stock_lot.status, "quarantined")

    def test_configured_coproduct_is_costed_and_traceable(self):
        bran = self.item("Bran", "by_product", "kg", track_lot=True)
        RecipeOutput.objects.create(
            profile_id=self.profile_id,
            recipe=self.recipe,
            output_item=bran,
            quantity=Decimal("5"),
            uom_code="kg",
            allocation_percent=Decimal("10"),
            is_primary=False,
        )
        self.receive(self.flour, "100", "2", "FLOUR-1")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-4",
        )
        ManufacturingService.release(order=order)
        ManufacturingService.issue_material(order=order, recipe_line=self.flour_line, quantity="60", idempotency_key="coproduct-input")
        primary = ManufacturingService.complete(order=order, actual_quantity="100", idempotency_key="coproduct-primary")
        coproduct = ManufacturingService.record_output(
            order=order,
            output_item=bran,
            quantity="5",
            output_kind="co_product",
            cost_allocation_percent="10",
            idempotency_key="coproduct-output",
        )
        self.assertEqual(coproduct.output_kind, "co_product")
        self.assertEqual(coproduct.unit_cost, Decimal("12") / Decimal("5"))
        self.assertIn(str(coproduct.stock_lot_id), str(ManufacturingService.traceability(order)))

    def test_material_reservation_scales_recipe_and_is_idempotent(self):
        self.receive(self.flour, "200", "2", "FLOUR-1")
        self.receive(self.yeast, "10", "5", "YEAST-1")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="200",
            order_number="PROD-5",
        )
        ManufacturingService.release(order=order)
        requirements = list(order.material_requirements.order_by("component_id"))
        self.assertEqual(len(requirements), 2)
        self.assertEqual(
            {requirement.required_quantity for requirement in requirements},
            {Decimal("120"), Decimal("4")},
        )
        reservations = ManufacturingService.reserve_materials(order=order)
        self.assertEqual(len(reservations), 2)
        self.assertTrue(order.material_requirements.filter(status=MaterialRequirement.Status.RESERVED).exists())
        self.assertEqual(len(ManufacturingService.reserve_materials(order=order)), 2)
        self.assertEqual(order.material_reservations.count(), 2)

    def test_material_shortage_is_visible_on_requirement_ledger(self):
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-SHORT",
        )
        ManufacturingService.release(order=order)
        self.assertEqual(ManufacturingService.reserve_materials(order=order), [])
        self.assertTrue(order.material_requirements.filter(status=MaterialRequirement.Status.SHORT).exists())

    def test_waste_ledger_is_idempotent_and_can_dispose_source_stock(self):
        receipt = self.receive(self.flour, "100", "2", "FLOUR-WASTE")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-WASTE",
        )
        ManufacturingService.release(order=order)
        ManufacturingService.issue_material(order=order, recipe_line=self.flour_line, quantity="60", idempotency_key="waste-input")
        first = ManufacturingService.record_waste(
            order=order,
            inventory_item=self.flour,
            source_stock_lot=receipt["stock_lot"],
            quantity="5",
            waste_type=WasteRecord.WasteType.SCRAP,
            disposition=WasteRecord.Disposition.DISCARDED,
            idempotency_key="waste-1",
            disposal_cost="10",
        )
        second = ManufacturingService.record_waste(
            order=order,
            inventory_item=self.flour,
            source_stock_lot=receipt["stock_lot"],
            quantity="5",
            idempotency_key="waste-1",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.net_cost, Decimal("20"))
        self.assertEqual(StockMovement.objects.filter(reference_type="production_waste").count(), 1)

    def test_multi_output_allocations_cannot_exceed_total_cost(self):
        bran = self.item("Bran 2", "by_product", "kg", track_lot=True)
        RecipeOutput.objects.create(
            profile_id=self.profile_id,
            recipe=self.recipe,
            output_item=bran,
            quantity=Decimal("5"),
            uom_code="kg",
            allocation_percent=Decimal("20"),
            is_primary=False,
        )
        self.receive(self.flour, "100", "2", "FLOUR-ALLOC")
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-ALLOC",
        )
        ManufacturingService.release(order=order)
        ManufacturingService.issue_material(order=order, recipe_line=self.flour_line, quantity="60", idempotency_key="alloc-input")
        primary = ManufacturingService.complete(order=order, actual_quantity="100", idempotency_key="alloc-primary")
        self.assertEqual(primary.cost_allocation_percent, Decimal("80"))
        with self.assertRaises(ManufacturingError):
            ManufacturingService.record_output(
                order=order,
                output_item=bran,
                quantity="5",
                cost_allocation_percent="30",
                idempotency_key="alloc-too-high",
            )

    def test_closed_costing_period_rejects_open_orders_and_new_costs(self):
        from django.utils import timezone
        today = timezone.localdate()
        period = CostingPeriod.objects.create(
            profile_id=self.profile_id,
            name="September 2026",
            period_start=today,
            period_end=today,
        )
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-PERIOD",
        )
        self.assertEqual(order.costing_period_id, period.id)
        with self.assertRaises(ManufacturingError):
            ManufacturingService.close_costing_period(period=period)
        order.status = ProductionOrderStatus.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        ManufacturingService.close_costing_period(period=period)
        order.refresh_from_db()
        with self.assertRaises(ManufacturingError):
            ManufacturingService.add_cost(order=order, cost_type="other", amount="1")

    def test_closed_costing_period_rejects_unresolved_nonconformance(self):
        from django.utils import timezone

        period = CostingPeriod.objects.create(
            profile_id=self.profile_id,
            name="Quality close guard",
            period_start=timezone.localdate(),
            period_end=timezone.localdate(),
        )
        order = ManufacturingService.create_production_order(
            profile_id=self.profile_id,
            recipe=self.recipe,
            stock_location=self.location,
            planned_quantity="100",
            order_number="PROD-NC-CLOSE",
        )
        order.status = ProductionOrderStatus.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        Nonconformance.objects.create(
            profile_id=self.profile_id,
            number="NC-CLOSE-1",
            production_order=order,
            severity="high",
            description="Awaiting corrective action.",
        )
        with self.assertRaises(ManufacturingError):
            ManufacturingService.close_costing_period(period=period)

        Nonconformance.objects.filter(production_order=order).update(status="closed")
        ManufacturingService.close_costing_period(period=period)
