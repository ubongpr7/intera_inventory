from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from mainapps.stock.models import StockLot, StockMovement, StockLotStatus, StockMovementType, TrackingType
from mainapps.stock.models import TrackingType
from subapps.services.stock_domain import StockDomainError, StockDomainService, _normalize_item_quantity

from .models import (
    MaterialIssue,
    MaterialRequirement,
    MaterialReservation,
    CostingPeriod,
    CostingMethod,
    ProductionCostEntry,
    ProductionOrder,
    ProductionOrderStatus,
    ProductionOperation,
    ProductionOperationStatus,
    ProductionOutput,
    QualityInspection,
    QualityInspectionStatus,
    NonconformanceStatus,
    Recipe,
    RecipeOperation,
    RecipeLine,
    RecipeOutput,
    RecipeStatus,
    WasteRecord,
)


class ManufacturingError(ValueError):
    pass


def _decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


class ManufacturingService:
    @staticmethod
    def _assert_profile(obj, profile_id):
        if str(obj.profile_id) != str(profile_id):
            raise ManufacturingError("The resource belongs to a different workspace.")

    @classmethod
    @transaction.atomic
    def create_production_order(cls, *, profile_id, recipe: Recipe, stock_location, planned_quantity, order_number, idempotency_key="", actor_user_id=None, notes=""):
        cls._assert_profile(recipe, profile_id)
        if idempotency_key:
            existing = ProductionOrder.objects.filter(profile_id=profile_id, idempotency_key=idempotency_key).first()
            if existing:
                return existing
        if recipe.status != RecipeStatus.ACTIVE:
            raise ManufacturingError("Only an active recipe can be released for production.")
        if stock_location.profile_id != profile_id:
            raise ManufacturingError("The stock location belongs to a different workspace.")
        quantity = _decimal(planned_quantity)
        if quantity <= 0:
            raise ManufacturingError("Planned quantity must be greater than zero.")
        costing_period = CostingPeriod.objects.filter(
            profile_id=profile_id,
            status=CostingPeriod.Status.OPEN,
            period_start__lte=timezone.localdate(),
            period_end__gte=timezone.localdate(),
        ).order_by("-period_start").first()
        return ProductionOrder.objects.create(
            profile_id=profile_id,
            recipe=recipe,
            finished_good=recipe.finished_good,
            stock_location=stock_location,
            order_number=order_number,
            planned_quantity=quantity,
            notes=notes or "",
            idempotency_key=idempotency_key or "",
            costing_period=costing_period,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

    @classmethod
    @transaction.atomic
    def release(cls, *, order: ProductionOrder, actor_user_id=None):
        if order.status != ProductionOrderStatus.DRAFT:
            raise ManufacturingError("Only draft production orders can be released.")
        cls.plan_material_requirements(order=order, actor_user_id=actor_user_id)
        order.status = ProductionOrderStatus.RELEASED
        order.updated_by_user_id = actor_user_id
        order.save(update_fields=["status", "updated_by_user_id", "updated_at"])
        ProductionOperation.objects.bulk_create([
            ProductionOperation(
                profile_id=order.profile_id,
                production_order=order,
                recipe_operation=operation,
                sequence=operation.sequence,
                name=operation.name,
                work_center=operation.work_center,
                planned_minutes=operation.expected_minutes,
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
            for operation in order.recipe.operations.select_related("work_center").all()
        ])
        return order

    @classmethod
    @transaction.atomic
    def plan_material_requirements(cls, *, order: ProductionOrder, actor_user_id=None):
        scale = order.planned_quantity / order.recipe.output_quantity
        requirements = []
        for line in order.recipe.lines.select_related("component").all():
            if line.line_type == RecipeLine.LineType.BY_PRODUCT or line.is_optional:
                continue
            quantity = line.quantity * scale * (Decimal("1") + (line.scrap_percent / Decimal("100")))
            requirement, _ = MaterialRequirement.objects.update_or_create(
                production_order=order,
                recipe_line=line,
                defaults={
                    "profile_id": order.profile_id,
                    "component": line.component,
                    "required_quantity": quantity,
                    "quantity_uom_code": line.uom_code,
                    "created_by_user_id": actor_user_id,
                    "updated_by_user_id": actor_user_id,
                },
            )
            requirements.append(requirement)
        return requirements

    @classmethod
    @transaction.atomic
    def start_operation(cls, *, operation: ProductionOperation, actor_user_id=None):
        if operation.status != ProductionOperationStatus.PENDING:
            raise ManufacturingError("Only pending operations can start.")
        previous = operation.production_order.operations.filter(sequence__lt=operation.sequence).exclude(status=ProductionOperationStatus.COMPLETED).exists()
        if previous:
            raise ManufacturingError("Previous production operations must be completed first.")
        operation.status = ProductionOperationStatus.IN_PROGRESS
        operation.started_at = timezone.now()
        operation.updated_by_user_id = actor_user_id
        operation.save(update_fields=["status", "started_at", "updated_by_user_id", "updated_at"])
        return operation

    @classmethod
    @transaction.atomic
    def complete_operation(cls, *, operation: ProductionOperation, actual_minutes, completed_quantity=0, scrap_quantity=0, actor_user_id=None, notes=""):
        if operation.status != ProductionOperationStatus.IN_PROGRESS:
            raise ManufacturingError("Only active operations can be completed.")
        actual_minutes = int(actual_minutes)
        if actual_minutes < 0:
            raise ManufacturingError("Actual minutes cannot be negative.")
        operation.status = ProductionOperationStatus.COMPLETED
        operation.actual_minutes = actual_minutes
        operation.completed_quantity = _decimal(completed_quantity)
        operation.scrap_quantity = _decimal(scrap_quantity)
        operation.completed_at = timezone.now()
        operation.notes = notes or operation.notes
        operation.updated_by_user_id = actor_user_id
        operation.save(update_fields=["status", "actual_minutes", "completed_at", "notes", "updated_by_user_id", "updated_at"])
        work_center = operation.work_center
        if work_center.hourly_labor_rate:
            cls.add_cost(
                order=operation.production_order,
                cost_type=ProductionCostEntry.CostType.LABOR,
                amount=(Decimal(actual_minutes) / Decimal("60")) * work_center.hourly_labor_rate,
                description=f"Labor for operation {operation.name}",
                actor_user_id=actor_user_id,
            )
        if work_center.hourly_overhead_rate:
            cls.add_cost(
                order=operation.production_order,
                cost_type=ProductionCostEntry.CostType.OVERHEAD,
                amount=(Decimal(actual_minutes) / Decimal("60")) * work_center.hourly_overhead_rate,
                description=f"Overhead for operation {operation.name}",
                actor_user_id=actor_user_id,
            )
        return operation

    @classmethod
    @transaction.atomic
    def reserve_materials(cls, *, order: ProductionOrder, actor_user_id=None, expires_at=None):
        if order.status not in (ProductionOrderStatus.RELEASED, ProductionOrderStatus.IN_PROGRESS):
            raise ManufacturingError("Materials can only be reserved for released or active production.")
        requirements = {requirement.recipe_line_id: requirement for requirement in cls.plan_material_requirements(order=order, actor_user_id=actor_user_id)}
        reservations = []
        scale = order.planned_quantity / order.recipe.output_quantity
        for line in order.recipe.lines.select_related("component").all():
            if line.line_type == RecipeLine.LineType.BY_PRODUCT or line.is_optional:
                continue
            existing = MaterialReservation.objects.filter(production_order=order, recipe_line=line).first()
            if existing:
                requirement = requirements.get(line.id)
                if requirement:
                    requirement.reserved_quantity = existing.quantity
                    requirement.status = MaterialRequirement.Status.RESERVED
                    requirement.updated_by_user_id = actor_user_id
                    requirement.save(update_fields=["reserved_quantity", "status", "updated_by_user_id", "updated_at"])
                reservations.append(existing)
                continue
            quantity = line.quantity * scale * (Decimal("1") + (line.scrap_percent / Decimal("100")))
            try:
                result = StockDomainService.reserve_stock(
                    inventory_item=line.component,
                    stock_location=order.stock_location,
                    quantity=quantity,
                    quantity_uom_code=line.uom_code,
                    external_order_type="production_order",
                    external_order_id=str(order.id),
                    external_order_line_id=str(line.id),
                    actor_user_id=actor_user_id,
                    expires_at=expires_at,
                    notes=f"Reserved for production order {order.order_number}",
                )
            except StockDomainError as exc:
                requirement = requirements.get(line.id)
                if requirement:
                    requirement.status = MaterialRequirement.Status.SHORT
                    requirement.notes = str(exc)
                    requirement.updated_by_user_id = actor_user_id
                    requirement.save(update_fields=["status", "notes", "updated_by_user_id", "updated_at"])
                continue
            material_reservation = MaterialReservation.objects.create(
                profile_id=order.profile_id,
                production_order=order,
                recipe_line=line,
                stock_reservation=result["reservation"],
                quantity=quantity,
                quantity_uom_code=line.uom_code,
                created_by_user_id=actor_user_id,
                updated_by_user_id=actor_user_id,
            )
            requirement = requirements.get(line.id)
            if requirement:
                requirement.reserved_quantity = quantity
                requirement.status = MaterialRequirement.Status.RESERVED
                requirement.updated_by_user_id = actor_user_id
                requirement.save(update_fields=["reserved_quantity", "status", "updated_by_user_id", "updated_at"])
            reservations.append(material_reservation)
        return reservations

    @classmethod
    @transaction.atomic
    def create_quality_inspection(cls, *, order: ProductionOrder, output=None, inspected_quantity=0, defect_quantity=0, inspector_user_id=None, measurements=None, notes="", nonconformance_code=""):
        if output is not None and output.production_order_id != order.id:
            raise ManufacturingError("The output does not belong to this production order.")
        return QualityInspection.objects.create(
            profile_id=order.profile_id,
            production_order=order,
            output=output,
            inspected_quantity=_decimal(inspected_quantity),
            defect_quantity=_decimal(defect_quantity),
            nonconformance_code=nonconformance_code or "",
            inspector_user_id=inspector_user_id,
            measurements=measurements or {},
            notes=notes or "",
        )

    @classmethod
    @transaction.atomic
    def resolve_quality_inspection(cls, *, inspection: QualityInspection, status, disposition="", released_quantity=0, quarantined_quantity=0, rework_quantity=0, inspector_user_id=None, notes=""):
        if status not in (QualityInspectionStatus.PASSED, QualityInspectionStatus.FAILED, QualityInspectionStatus.RELEASED, QualityInspectionStatus.QUARANTINED):
            raise ManufacturingError("Invalid quality inspection resolution.")
        inspection.status = status
        inspection.inspector_user_id = inspector_user_id or inspection.inspector_user_id
        inspection.inspected_at = timezone.now()
        inspection.notes = notes or inspection.notes
        inspection.disposition = disposition or inspection.disposition
        inspection.released_quantity = _decimal(released_quantity)
        inspection.quarantined_quantity = _decimal(quarantined_quantity)
        inspection.rework_quantity = _decimal(rework_quantity)
        inspection.save(update_fields=["status", "inspector_user_id", "inspected_at", "notes", "disposition", "released_quantity", "quarantined_quantity", "rework_quantity", "updated_at"])
        if inspection.output_id and status in (QualityInspectionStatus.FAILED, QualityInspectionStatus.QUARANTINED):
            from mainapps.stock.models import StockLotStatus
            inspection.output.stock_lot.status = StockLotStatus.QUARANTINED
            inspection.output.stock_lot.save(update_fields=["status", "updated_at"])
        return inspection

    @classmethod
    @transaction.atomic
    def start(cls, *, order: ProductionOrder, actor_user_id=None):
        if order.status not in (ProductionOrderStatus.RELEASED, ProductionOrderStatus.IN_PROGRESS):
            raise ManufacturingError("Production must be released before it can start.")
        if order.status == ProductionOrderStatus.RELEASED:
            order.started_at = timezone.now()
        order.status = ProductionOrderStatus.IN_PROGRESS
        order.updated_by_user_id = actor_user_id
        order.save(update_fields=["status", "started_at", "updated_by_user_id", "updated_at"])
        return order

    @classmethod
    @transaction.atomic
    def issue_material(cls, *, order: ProductionOrder, recipe_line: RecipeLine, quantity, stock_lot=None, idempotency_key="", actor_user_id=None, notes=""):
        if order.status not in (ProductionOrderStatus.RELEASED, ProductionOrderStatus.IN_PROGRESS):
            raise ManufacturingError("Materials can only be issued for released or active production.")
        cls._assert_profile(recipe_line, order.profile_id)
        if recipe_line.recipe_id != order.recipe_id:
            raise ManufacturingError("The recipe line does not belong to this production order.")
        if recipe_line.component.profile_id != order.profile_id:
            raise ManufacturingError("The component belongs to a different workspace.")
        if stock_lot is not None and (
            stock_lot.profile_id != order.profile_id
            or stock_lot.inventory_item_id != recipe_line.component_id
        ):
            raise ManufacturingError("The selected stock lot does not belong to the component.")
        if idempotency_key:
            existing = MaterialIssue.objects.filter(profile_id=order.profile_id, idempotency_key=idempotency_key).first()
            if existing:
                return existing
        reference_id = f"{order.id}:{recipe_line.id}:{idempotency_key or timezone.now().timestamp()}"
        try:
            result = StockDomainService.issue_stock(
                inventory_item=recipe_line.component,
                stock_location=order.stock_location,
                quantity=quantity,
                quantity_uom_code=recipe_line.uom_code,
                actor_user_id=actor_user_id,
                stock_lot=stock_lot,
                reference_type="production_material_issue",
                reference_id=reference_id,
                notes=notes or f"Consumed for production order {order.order_number}",
                tracking_type=TrackingType.CONSUMED_IN_BUILD,
            )
        except StockDomainError as exc:
            raise ManufacturingError(str(exc)) from exc
        movement = StockMovement.objects.get(reference_type="production_material_issue", reference_id=reference_id)
        issue = MaterialIssue.objects.create(
            profile_id=order.profile_id,
            production_order=order,
            recipe_line=recipe_line,
            component=recipe_line.component,
            stock_lot=result.get("stock_lot"),
            stock_movement=movement,
            quantity=movement.quantity,
            quantity_uom_code=movement.quantity_uom_code,
            unit_cost=movement.unit_cost or Decimal("0"),
            notes=notes or "",
            idempotency_key=idempotency_key or "",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        requirement = MaterialRequirement.objects.filter(production_order=order, recipe_line=recipe_line).first()
        if requirement:
            requirement.issued_quantity = min(
                requirement.required_quantity,
                requirement.issued_quantity + movement.quantity,
            )
            requirement.status = (
                MaterialRequirement.Status.FULFILLED
                if requirement.issued_quantity >= requirement.required_quantity
                else MaterialRequirement.Status.PARTIALLY_ISSUED
            )
            requirement.updated_by_user_id = actor_user_id
            requirement.save(update_fields=["issued_quantity", "status", "updated_by_user_id", "updated_at"])
        if order.status == ProductionOrderStatus.RELEASED:
            order.status = ProductionOrderStatus.IN_PROGRESS
            order.started_at = order.started_at or timezone.now()
            order.updated_by_user_id = actor_user_id
            order.save(update_fields=["status", "started_at", "updated_by_user_id", "updated_at"])
        return issue

    @classmethod
    @transaction.atomic
    def add_cost(cls, *, order: ProductionOrder, cost_type, amount, currency_code="", description="", actor_user_id=None):
        if order.status in (ProductionOrderStatus.COMPLETED, ProductionOrderStatus.CANCELLED):
            raise ManufacturingError("Costs cannot be added after production is closed.")
        if order.costing_period_id and order.costing_period.status == CostingPeriod.Status.CLOSED:
            raise ManufacturingError("Costs cannot be added to a closed costing period.")
        amount = _decimal(amount)
        if amount < 0:
            raise ManufacturingError("Production cost cannot be negative.")
        return ProductionCostEntry.objects.create(
            profile_id=order.profile_id,
            production_order=order,
            cost_type=cost_type,
            amount=amount,
            currency_code=currency_code or "",
            description=description or "",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

    @classmethod
    def cost_summary(cls, order: ProductionOrder, quantity_override=None) -> dict:
        issues = list(order.material_issues.select_related("component").all())
        material_cost = sum((issue.total_cost for issue in issues), Decimal("0"))
        additional_cost = sum((entry.amount for entry in order.cost_entries.all()), Decimal("0"))
        waste_cost = sum((record.net_cost for record in order.waste_records.all()), Decimal("0"))
        output_quantity = sum((output.quantity for output in order.outputs.all()), Decimal("0"))

        # Issue rows retain the immutable lot cost selected by the stock domain.
        # This is the auditable basis for actual and FIFO production valuation.
        weighted_material_cost = Decimal("0")
        issues_by_component = {}
        for issue in issues:
            issues_by_component.setdefault(issue.component_id, []).append(issue)
        for component_id, component_issues in issues_by_component.items():
            issued_quantity = sum((issue.quantity for issue in component_issues), Decimal("0"))
            lot_pool = StockLot.objects.filter(profile_id=order.profile_id, inventory_item_id=component_id)
            lot_quantity = sum((lot.received_quantity for lot in lot_pool), Decimal("0"))
            lot_value = sum((lot.received_quantity * (lot.unit_cost or Decimal("0")) for lot in lot_pool), Decimal("0"))
            weighted_unit_cost = lot_value / lot_quantity if lot_quantity else (
                sum((issue.quantity * (issue.unit_cost or Decimal("0")) for issue in component_issues), Decimal("0")) / issued_quantity
                if issued_quantity else Decimal("0")
            )
            weighted_material_cost += issued_quantity * weighted_unit_cost

        actual_cost = material_cost + additional_cost + waste_cost
        quantity_for_standard = _decimal(quantity_override or order.actual_quantity or order.planned_quantity)
        standard_cost = (order.recipe.standard_unit_cost or Decimal("0")) * quantity_for_standard
        weighted_average_cost = weighted_material_cost + additional_cost + waste_cost
        costing_method = order.recipe.costing_method
        if costing_method == CostingMethod.STANDARD and standard_cost > 0:
            total_cost = standard_cost
        elif costing_method == CostingMethod.WEIGHTED_AVERAGE:
            total_cost = weighted_average_cost
        else:
            total_cost = actual_cost
        allocation_percent_total = sum((output.cost_allocation_percent for output in order.outputs.all()), Decimal("0"))
        return {
            "material_cost": material_cost,
            "additional_cost": additional_cost,
            "waste_cost": waste_cost,
            "actual_cost": actual_cost,
            "weighted_average_cost": weighted_average_cost,
            "standard_cost": standard_cost,
            "variance": actual_cost - standard_cost if standard_cost else Decimal("0"),
            "costing_method": costing_method,
            "total_cost": total_cost,
            "output_quantity": output_quantity,
            "unit_cost": total_cost / output_quantity if output_quantity else Decimal("0"),
            "allocation_percent_total": allocation_percent_total,
            "allocation_valid": allocation_percent_total <= Decimal("100"),
            "unallocated_cost": total_cost * max(Decimal("100") - allocation_percent_total, Decimal("0")) / Decimal("100"),
            "currency_code": next((entry.currency_code for entry in order.cost_entries.all() if entry.currency_code), ""),
        }

    @classmethod
    def traceability(cls, order: ProductionOrder) -> dict:
        return {
            "production_order_id": str(order.id),
            "inputs": [
                {
                    "issue_id": str(issue.id),
                    "component_id": str(issue.component_id),
                    "component_name": issue.component.name_snapshot,
                    "stock_lot_id": str(issue.stock_lot_id) if issue.stock_lot_id else None,
                    "quantity": issue.quantity,
                    "quantity_uom_code": issue.quantity_uom_code,
                    "unit_cost": issue.unit_cost,
                    "issued_at": issue.issued_at,
                }
                for issue in order.material_issues.select_related("component", "stock_lot").all()
            ],
            "outputs": [
                {
                    "output_id": str(output.id),
                    "output_item_id": str(output.output_item_id),
                    "output_item_name": output.output_item.name_snapshot,
                    "stock_lot_id": str(output.stock_lot_id),
                    "quantity": output.quantity,
                    "quantity_uom_code": output.quantity_uom_code,
                    "unit_cost": output.unit_cost,
                    "output_kind": output.output_kind,
                    "cost_allocation_percent": output.cost_allocation_percent,
                    "produced_at": output.produced_at,
                }
                for output in order.outputs.select_related("output_item", "stock_lot").all()
            ],
            "waste": [
                {
                    "waste_id": str(record.id),
                    "inventory_item_id": str(record.inventory_item_id),
                    "inventory_item_name": record.inventory_item.name_snapshot,
                    "source_stock_lot_id": str(record.source_stock_lot_id) if record.source_stock_lot_id else None,
                    "stock_movement_id": str(record.stock_movement_id) if record.stock_movement_id else None,
                    "quantity": record.quantity,
                    "quantity_uom_code": record.quantity_uom_code,
                    "unit_cost": record.unit_cost,
                    "net_cost": record.net_cost,
                    "waste_type": record.waste_type,
                    "disposition": record.disposition,
                    "recorded_at": record.recorded_at,
                }
                for record in order.waste_records.select_related("inventory_item", "source_stock_lot", "stock_movement").all()
            ],
        }

    @classmethod
    def traceability_for_lot(cls, *, profile_id, stock_lot):
        issues = MaterialIssue.objects.filter(profile_id=profile_id, stock_lot=stock_lot).select_related("production_order", "component")
        outputs = ProductionOutput.objects.filter(profile_id=profile_id, stock_lot=stock_lot).select_related("production_order", "output_item")
        return {
            "stock_lot_id": str(stock_lot.id),
            "inventory_item_id": str(stock_lot.inventory_item_id),
            "inputs": [
                {
                    "issue_id": str(issue.id),
                    "production_order_id": str(issue.production_order_id),
                    "order_number": issue.production_order.order_number,
                    "component_id": str(issue.component_id),
                    "component_name": issue.component.name_snapshot,
                    "quantity": issue.quantity,
                    "unit_cost": issue.unit_cost,
                }
                for issue in issues
            ],
            "outputs": [
                {
                    "output_id": str(output.id),
                    "production_order_id": str(output.production_order_id),
                    "order_number": output.production_order.order_number,
                    "output_item_id": str(output.output_item_id),
                    "output_item_name": output.output_item.name_snapshot,
                    "quantity": output.quantity,
                    "output_kind": output.output_kind,
                    "unit_cost": output.unit_cost,
                }
                for output in outputs
            ],
        }

    @classmethod
    @transaction.atomic
    def record_output(
        cls,
        *,
        order: ProductionOrder,
        output_item,
        quantity,
        output_kind=ProductionOutput.OutputKind.CO_PRODUCT,
        cost_allocation_percent=Decimal("0"),
        quantity_uom_code="",
        lot_number="",
        expiry_date=None,
        idempotency_key="",
        actor_user_id=None,
        notes="",
    ):
        if order.status not in (ProductionOrderStatus.IN_PROGRESS, ProductionOrderStatus.COMPLETED):
            raise ManufacturingError("Additional outputs can only be recorded for active or completed production.")
        if output_kind == ProductionOutput.OutputKind.WASTE:
            raise ManufacturingError("Waste must be recorded through the waste ledger before it can affect inventory.")
        if output_item.profile_id != order.profile_id:
            raise ManufacturingError("The output item belongs to a different workspace.")
        if idempotency_key:
            existing = ProductionOutput.objects.filter(profile_id=order.profile_id, idempotency_key=idempotency_key).first()
            if existing:
                return existing
        allowed = order.recipe.outputs.filter(output_item=output_item).first()
        if allowed is None and output_item.id != order.finished_good_id:
            raise ManufacturingError("The output item is not configured on the recipe.")
        allocation = _decimal(cost_allocation_percent)
        if allocation < 0 or allocation > 100:
            raise ManufacturingError("Cost allocation must be between 0 and 100 percent.")
        existing_allocation = sum(
            (output.cost_allocation_percent for output in order.outputs.all()),
            Decimal("0"),
        )
        if existing_allocation + allocation > Decimal("100"):
            raise ManufacturingError("Output cost allocations cannot exceed 100 percent.")
        quantity = _decimal(quantity)
        normalized_quantity, _, resolved_uom = _normalize_item_quantity(
            output_item,
            quantity,
            uom_code=quantity_uom_code or output_item.stock_uom_code or output_item.default_uom_code or "",
        )
        total_cost = cls.cost_summary(order)["total_cost"]
        allocated_cost = total_cost * allocation / Decimal("100")
        unit_cost = allocated_cost / normalized_quantity if normalized_quantity else Decimal("0")
        try:
            result = StockDomainService.receive_stock(
                inventory_item=output_item,
                stock_location=order.stock_location,
                quantity=normalized_quantity,
                quantity_uom_code=resolved_uom,
                unit_cost=unit_cost,
                actor_user_id=actor_user_id,
                lot_number=lot_number or f"PROD-{order.order_number}-{output_kind}",
                expiry_date=expiry_date,
                force_lot=True,
                reference_type="production_output",
                reference_id=f"{order.id}:{idempotency_key or timezone.now().timestamp()}",
                notes=notes or f"Recorded {output_kind} for production order {order.order_number}",
            )
        except StockDomainError as exc:
            raise ManufacturingError(str(exc)) from exc
        return ProductionOutput.objects.create(
            profile_id=order.profile_id,
            production_order=order,
            output_item=output_item,
            stock_lot=result["stock_lot"],
            quantity=quantity,
            quantity_uom_code=resolved_uom,
            unit_cost=unit_cost,
            output_kind=output_kind,
            cost_allocation_percent=allocation,
            notes=notes or "",
            idempotency_key=idempotency_key or "",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

    @classmethod
    @transaction.atomic
    def complete(cls, *, order: ProductionOrder, actual_quantity, scrap_quantity=0, lot_number="", expiry_date=None, idempotency_key="", actor_user_id=None, notes=""):
        if idempotency_key:
            existing = ProductionOutput.objects.filter(profile_id=order.profile_id, idempotency_key=idempotency_key).first()
            if existing:
                return existing
        if order.status not in (ProductionOrderStatus.RELEASED, ProductionOrderStatus.IN_PROGRESS):
            raise ManufacturingError("Only released or active production can be completed.")
        actual_quantity = _decimal(actual_quantity)
        scrap_quantity = _decimal(scrap_quantity)
        if actual_quantity <= 0 or scrap_quantity < 0:
            raise ManufacturingError("Actual output must be positive and scrap cannot be negative.")
        total_cost = cls.cost_summary(order, quantity_override=actual_quantity)["total_cost"]
        configured_outputs = list(order.recipe.outputs.all())
        non_primary_allocation = sum(
            (output.allocation_percent for output in configured_outputs if not output.is_primary),
            Decimal("0"),
        )
        configured_primary = next((output.allocation_percent for output in configured_outputs if output.is_primary), None)
        primary_allocation = configured_primary if configured_primary is not None else Decimal("100") - non_primary_allocation
        if primary_allocation < 0 or non_primary_allocation + primary_allocation > Decimal("100"):
            raise ManufacturingError("Recipe output cost allocations must total no more than 100 percent.")
        output_uom_code = order.recipe.output_uom_code or order.finished_good.stock_uom_code or order.finished_good.default_uom_code or ""
        output_stock_quantity, _, output_uom_code = _normalize_item_quantity(
            order.finished_good,
            actual_quantity,
            uom_code=output_uom_code,
        )
        unit_cost = (total_cost * primary_allocation / Decimal("100")) / output_stock_quantity if output_stock_quantity else Decimal("0")
        try:
            result = StockDomainService.receive_stock(
                inventory_item=order.finished_good,
                stock_location=order.stock_location,
                quantity=output_stock_quantity,
                quantity_uom_code=output_uom_code,
                unit_cost=unit_cost,
                actor_user_id=actor_user_id,
                lot_number=lot_number or f"PROD-{order.order_number}",
                expiry_date=expiry_date,
                force_lot=True,
                reference_type="production_output",
                reference_id=str(order.id),
                notes=notes or f"Completed production order {order.order_number}",
            )
        except StockDomainError as exc:
            raise ManufacturingError(str(exc)) from exc
        output = ProductionOutput.objects.create(
            profile_id=order.profile_id,
            production_order=order,
            output_item=order.finished_good,
            stock_lot=result["stock_lot"],
            quantity=actual_quantity,
            quantity_uom_code=output_uom_code,
            unit_cost=result["movement"].unit_cost or unit_cost,
            output_kind=ProductionOutput.OutputKind.PRIMARY,
            cost_allocation_percent=primary_allocation,
            notes=notes or "",
            idempotency_key=idempotency_key or "",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )
        order.actual_quantity = actual_quantity
        order.scrap_quantity = scrap_quantity
        order.status = ProductionOrderStatus.COMPLETED
        order.completed_at = timezone.now()
        order.updated_by_user_id = actor_user_id
        order.save(update_fields=["actual_quantity", "scrap_quantity", "status", "completed_at", "updated_by_user_id", "updated_at"])
        return output

    @classmethod
    @transaction.atomic
    def record_waste(
        cls,
        *,
        order: ProductionOrder,
        inventory_item,
        quantity,
        waste_type=WasteRecord.WasteType.SCRAP,
        disposition=WasteRecord.Disposition.DISCARDED,
        quantity_uom_code="",
        source_stock_lot=None,
        production_operation=None,
        unit_cost=None,
        disposal_cost=0,
        recovery_value=0,
        reason_code="",
        idempotency_key="",
        actor_user_id=None,
        notes="",
    ):
        if order.status not in (ProductionOrderStatus.IN_PROGRESS, ProductionOrderStatus.COMPLETED):
            raise ManufacturingError("Waste can only be recorded for active or completed production.")
        if inventory_item.profile_id != order.profile_id:
            raise ManufacturingError("The waste item belongs to a different workspace.")
        if production_operation is not None and production_operation.production_order_id != order.id:
            raise ManufacturingError("The operation does not belong to this production order.")
        if source_stock_lot is not None and source_stock_lot.inventory_item_id != inventory_item.id:
            raise ManufacturingError("The source lot does not belong to the waste item.")
        if idempotency_key:
            existing = WasteRecord.objects.filter(profile_id=order.profile_id, idempotency_key=idempotency_key).first()
            if existing:
                return existing
        quantity = _decimal(quantity)
        if quantity <= 0:
            raise ManufacturingError("Waste quantity must be greater than zero.")
        normalized_quantity, _, resolved_uom = _normalize_item_quantity(
            inventory_item,
            quantity,
            uom_code=quantity_uom_code or inventory_item.stock_uom_code or inventory_item.default_uom_code or "",
        )
        if unit_cost is None:
            unit_cost = source_stock_lot.unit_cost if source_stock_lot is not None else Decimal("0")
        unit_cost = _decimal(unit_cost)
        disposal_cost = _decimal(disposal_cost)
        recovery_value = _decimal(recovery_value)
        if min(unit_cost, disposal_cost, recovery_value) < 0:
            raise ManufacturingError("Waste costs and recovery value cannot be negative.")
        movement = None
        reference_id = f"{order.id}:{idempotency_key or timezone.now().timestamp()}"
        if disposition == WasteRecord.Disposition.DISCARDED and source_stock_lot is not None:
            try:
                result = StockDomainService.issue_stock(
                    inventory_item=inventory_item,
                    stock_location=order.stock_location,
                    quantity=normalized_quantity,
                    quantity_uom_code=resolved_uom,
                    actor_user_id=actor_user_id,
                    stock_lot=source_stock_lot,
                    reference_type="production_waste",
                    reference_id=reference_id,
                    notes=notes or f"Disposed as waste for production order {order.order_number}",
                    tracking_type=TrackingType.DAMAGE_REPORTED,
                )
            except StockDomainError as exc:
                raise ManufacturingError(str(exc)) from exc
            movement = StockMovement.objects.filter(
                profile_id=order.profile_id,
                reference_type="production_waste",
                reference_id=reference_id,
            ).order_by("-created_at").first()
        return WasteRecord.objects.create(
            profile_id=order.profile_id,
            production_order=order,
            production_operation=production_operation,
            inventory_item=inventory_item,
            source_stock_lot=source_stock_lot,
            stock_movement=movement,
            quantity=quantity,
            quantity_uom_code=resolved_uom,
            unit_cost=unit_cost,
            disposal_cost=disposal_cost,
            recovery_value=recovery_value,
            waste_type=waste_type,
            disposition=disposition,
            reason_code=reason_code or "",
            notes=notes or "",
            idempotency_key=idempotency_key or "",
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
        )

    @classmethod
    @transaction.atomic
    def close_costing_period(cls, *, period: CostingPeriod, actor_user_id=None, notes=""):
        if period.status == CostingPeriod.Status.CLOSED:
            return period
        open_orders = period.production_orders.filter(
            status__in=[ProductionOrderStatus.DRAFT, ProductionOrderStatus.RELEASED, ProductionOrderStatus.IN_PROGRESS]
        ).exists()
        if open_orders:
            raise ManufacturingError("All production orders in a costing period must be completed or cancelled before closing it.")
        unresolved_quality = period.production_orders.filter(
            Q(nonconformances__status__in=[NonconformanceStatus.OPEN, NonconformanceStatus.CONTAINED, NonconformanceStatus.INVESTIGATING, NonconformanceStatus.CORRECTIVE_ACTION])
        ).exists()
        if unresolved_quality:
            raise ManufacturingError("All quality nonconformances in a costing period must be resolved before closing it.")
        incomplete_allocation = any(
            not cls.cost_summary(order)["allocation_valid"]
            for order in period.production_orders.exclude(status=ProductionOrderStatus.CANCELLED)
        )
        if incomplete_allocation:
            raise ManufacturingError("All production output cost allocations must be valid before closing the period.")
        period.status = CostingPeriod.Status.CLOSED
        period.closed_at = timezone.now()
        period.closed_by_user_id = actor_user_id
        period.notes = notes or period.notes
        period.save(update_fields=["status", "closed_at", "closed_by_user_id", "notes", "updated_at"])
        return period
