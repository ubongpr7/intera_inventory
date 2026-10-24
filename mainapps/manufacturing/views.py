from datetime import date, datetime, time, timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from mainapps.stock.models import StockLocation, StockLot
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset, HasModelRequestPermission
from subapps.permissions.constants import UNIFIED_PERMISSION_DICT
from subapps.utils.request_context import get_request_profile_id, get_request_user_id, scope_queryset_by_identity
from mainapps.identity.idempotency import request_idempotency_key

from .models import CostingPeriod, Nonconformance, ProductionCostEntry, ProductionOperation, ProductionOrder, ProductionOutput, QualityInspection, Recipe, RecipeLine, WasteRecord, WorkCenter, WorkCenterBlock
from .serializers import (
    MaterialIssueSerializer,
    ProductionCostEntrySerializer,
    ProductionOrderSerializer,
    ProductionOutputSerializer,
    ProductionOperationSerializer,
    QualityInspectionSerializer,
    RecipeSerializer,
    MaterialReservationSerializer,
    MaterialRequirementSerializer,
    WasteRecordSerializer,
    CostingPeriodSerializer,
    WorkCenterSerializer,
    NonconformanceSerializer,
    WorkCenterBlockSerializer,
)
from .services import ManufacturingError, ManufacturingService


class ManufacturingScopedMixin:
    def profile_id(self):
        value = get_request_profile_id(self.request)
        if value is None:
            raise ManufacturingError("An active workspace profile is required.")
        return value


class RecipeViewSet(ManufacturingScopedMixin, BaseCachePermissionViewset):
    queryset = Recipe.objects.select_related("finished_good").prefetch_related("lines__component", "outputs__output_item", "operations__work_center")
    serializer_class = RecipeSerializer
    filterset_fields = ["finished_good", "status", "version"]
    search_fields = ["name", "finished_good__name_snapshot"]
    ordering_fields = ["created_at", "version", "name"]
    CACHE_ENABLED = False
    required_permission = UNIFIED_PERMISSION_DICT["manufacturing"]

    def get_queryset(self):
        return scope_queryset_by_identity(super().get_queryset(), canonical_field="profile_id", legacy_field="profile", value=self.profile_id())

    def perform_create(self, serializer):
        profile_id = self.profile_id()
        idempotency_key = request_idempotency_key(self.request, required=True)
        finished_good = serializer.validated_data["finished_good"]
        if finished_good.profile_id != profile_id:
            raise ManufacturingError("The finished good belongs to a different workspace.")
        serializer.save(profile_id=profile_id, created_by_user_id=get_request_user_id(self.request))

    def perform_update(self, serializer):
        serializer.save(updated_by_user_id=get_request_user_id(self.request))


class ProductionOrderViewSet(ManufacturingScopedMixin, BaseCachePermissionViewset):
    queryset = ProductionOrder.objects.select_related("recipe", "finished_good", "stock_location").prefetch_related("material_issues", "outputs", "cost_entries", "operations", "quality_inspections")
    serializer_class = ProductionOrderSerializer
    filterset_fields = ["status", "recipe", "finished_good", "stock_location"]
    search_fields = ["order_number", "finished_good__name_snapshot"]
    ordering_fields = ["created_at", "order_number", "status"]
    CACHE_ENABLED = False
    required_permission = UNIFIED_PERMISSION_DICT["manufacturing"]

    def get_queryset(self):
        return scope_queryset_by_identity(super().get_queryset(), canonical_field="profile_id", legacy_field="profile", value=self.profile_id())

    def perform_create(self, serializer):
        profile_id = self.profile_id()
        idempotency_key = request_idempotency_key(self.request, required=True)
        recipe = serializer.validated_data["recipe"]
        location = serializer.validated_data["stock_location"]
        order = ManufacturingService.create_production_order(
            profile_id=profile_id,
            recipe=recipe,
            stock_location=location,
            planned_quantity=serializer.validated_data["planned_quantity"],
            order_number=serializer.validated_data["order_number"],
            idempotency_key=idempotency_key,
            actor_user_id=get_request_user_id(self.request),
            notes=serializer.validated_data.get("notes", ""),
        )
        serializer.instance = order

    def _action_response(self, action):
        try:
            order = self.get_object()
            return Response(ProductionOrderSerializer(action(order=order, actor_user_id=get_request_user_id(self.request)), context={"request": self.request}).data)
        except ManufacturingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        return self._action_response(ManufacturingService.release)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._action_response(ManufacturingService.start)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if order.status in ("completed", "cancelled"):
            return Response({"detail": "Production is already closed."}, status=status.HTTP_400_BAD_REQUEST)
        order.status = "cancelled"
        order.updated_by_user_id = get_request_user_id(request)
        order.save(update_fields=["status", "updated_by_user_id", "updated_at"])
        return Response(ProductionOrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def issue_material(self, request, pk=None):
        order = self.get_object()
        try:
            line = RecipeLine.objects.get(pk=request.data.get("recipe_line"), recipe=order.recipe, profile_id=order.profile_id)
            stock_lot = StockLot.objects.filter(pk=request.data.get("stock_lot"), profile_id=order.profile_id).first() if request.data.get("stock_lot") else None
            issue = ManufacturingService.issue_material(
                order=order,
                recipe_line=line,
                quantity=request.data.get("quantity"),
                stock_lot=stock_lot,
                idempotency_key=request.data.get("idempotency_key", ""),
                actor_user_id=get_request_user_id(request),
                notes=request.data.get("notes", ""),
            )
            return Response(MaterialIssueSerializer(issue).data, status=status.HTTP_201_CREATED)
        except (RecipeLine.DoesNotExist, ManufacturingError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def add_cost(self, request, pk=None):
        order = self.get_object()
        try:
            entry = ManufacturingService.add_cost(
                order=order,
                cost_type=request.data.get("cost_type"),
                amount=request.data.get("amount"),
                currency_code=request.data.get("currency_code", ""),
                description=request.data.get("description", ""),
                actor_user_id=get_request_user_id(request),
            )
            return Response(ProductionCostEntrySerializer(entry).data, status=status.HTTP_201_CREATED)
        except ManufacturingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        order = self.get_object()
        try:
            output = ManufacturingService.complete(
                order=order,
                actual_quantity=request.data.get("actual_quantity"),
                scrap_quantity=request.data.get("scrap_quantity", 0),
                lot_number=request.data.get("lot_number", ""),
                expiry_date=request.data.get("expiry_date"),
                idempotency_key=request.data.get("idempotency_key", ""),
                actor_user_id=get_request_user_id(request),
                notes=request.data.get("notes", ""),
            )
            return Response(ProductionOutputSerializer(output).data, status=status.HTTP_201_CREATED)
        except ManufacturingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def record_output(self, request, pk=None):
        order = self.get_object()
        try:
            output_item = order.recipe.outputs.filter(output_item_id=request.data.get("output_item")).values_list("output_item", flat=True).first()
            if output_item is None:
                output_item = request.data.get("output_item")
            from mainapps.inventory.models import InventoryItem
            item = InventoryItem.objects.get(pk=output_item, profile_id=order.profile_id)
            output = ManufacturingService.record_output(
                order=order,
                output_item=item,
                quantity=request.data.get("quantity"),
                output_kind=request.data.get("output_kind", ProductionOutput.OutputKind.CO_PRODUCT),
                cost_allocation_percent=request.data.get("cost_allocation_percent", 0),
                quantity_uom_code=request.data.get("quantity_uom_code", ""),
                lot_number=request.data.get("lot_number", ""),
                expiry_date=request.data.get("expiry_date"),
                idempotency_key=request.data.get("idempotency_key", ""),
                actor_user_id=get_request_user_id(request),
                notes=request.data.get("notes", ""),
            )
            return Response(ProductionOutputSerializer(output).data, status=status.HTTP_201_CREATED)
        except (InventoryItem.DoesNotExist, ManufacturingError, TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["get"])
    def cost_summary(self, request, pk=None):
        order = self.get_object()
        return Response(ManufacturingService.cost_summary(order))

    @action(detail=True, methods=["get"])
    def traceability(self, request, pk=None):
        order = self.get_object()
        return Response(ManufacturingService.traceability(order))

    @action(detail=True, methods=["get"])
    def operations(self, request, pk=None):
        return Response(ProductionOperationSerializer(self.get_object().operations.all(), many=True).data)

    @action(detail=True, methods=["post"], url_path=r"operations/(?P<operation_id>[^/.]+)/start")
    def start_operation(self, request, pk=None, operation_id=None):
        try:
            operation = self.get_object().operations.get(pk=operation_id)
            operation = ManufacturingService.start_operation(operation=operation, actor_user_id=get_request_user_id(request))
            return Response(ProductionOperationSerializer(operation).data)
        except (ProductionOperation.DoesNotExist, ManufacturingError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path=r"operations/(?P<operation_id>[^/.]+)/complete")
    def complete_operation(self, request, pk=None, operation_id=None):
        try:
            operation = self.get_object().operations.get(pk=operation_id)
            operation = ManufacturingService.complete_operation(
                operation=operation,
                actual_minutes=request.data.get("actual_minutes", operation.planned_minutes),
                completed_quantity=request.data.get("completed_quantity", 0),
                scrap_quantity=request.data.get("scrap_quantity", 0),
                actor_user_id=get_request_user_id(request),
                notes=request.data.get("notes", ""),
            )
            return Response(ProductionOperationSerializer(operation).data)
        except (ProductionOperation.DoesNotExist, ManufacturingError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def reserve_materials(self, request, pk=None):
        order = self.get_object()
        try:
            reservations = ManufacturingService.reserve_materials(
                order=order,
                actor_user_id=get_request_user_id(request),
            )
            return Response(MaterialReservationSerializer(reservations, many=True).data, status=status.HTTP_201_CREATED)
        except ManufacturingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["get"])
    def requirements(self, request, pk=None):
        order = self.get_object()
        return Response(MaterialRequirementSerializer(order.material_requirements.all(), many=True).data)

    @action(detail=True, methods=["post"])
    def record_waste(self, request, pk=None):
        order = self.get_object()
        try:
            from mainapps.inventory.models import InventoryItem
            item = InventoryItem.objects.get(pk=request.data.get("inventory_item"), profile_id=order.profile_id)
            source_lot = StockLot.objects.filter(pk=request.data.get("source_stock_lot"), profile_id=order.profile_id).first() if request.data.get("source_stock_lot") else None
            operation = order.operations.filter(pk=request.data.get("production_operation")).first() if request.data.get("production_operation") else None
            record = ManufacturingService.record_waste(
                order=order,
                inventory_item=item,
                quantity=request.data.get("quantity"),
                waste_type=request.data.get("waste_type", WasteRecord.WasteType.SCRAP),
                disposition=request.data.get("disposition", WasteRecord.Disposition.DISCARDED),
                quantity_uom_code=request.data.get("quantity_uom_code", ""),
                source_stock_lot=source_lot,
                production_operation=operation,
                unit_cost=request.data.get("unit_cost"),
                disposal_cost=request.data.get("disposal_cost", 0),
                recovery_value=request.data.get("recovery_value", 0),
                reason_code=request.data.get("reason_code", ""),
                idempotency_key=request.data.get("idempotency_key", ""),
                actor_user_id=get_request_user_id(request),
                notes=request.data.get("notes", ""),
            )
            return Response(WasteRecordSerializer(record).data, status=status.HTTP_201_CREATED)
        except (InventoryItem.DoesNotExist, ManufacturingError, TypeError, ValueError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def inspect(self, request, pk=None):
        order = self.get_object()
        output = order.outputs.filter(pk=request.data.get("output")).first() if request.data.get("output") else None
        try:
            inspection = ManufacturingService.create_quality_inspection(
                order=order,
                output=output,
                inspected_quantity=request.data.get("inspected_quantity", 0),
                defect_quantity=request.data.get("defect_quantity", 0),
                inspector_user_id=get_request_user_id(request),
                measurements=request.data.get("measurements", {}),
                notes=request.data.get("notes", ""),
                nonconformance_code=request.data.get("nonconformance_code", ""),
            )
            if request.data.get("nonconformance_code") or inspection.defect_quantity > 0:
                Nonconformance.objects.create(
                    profile_id=order.profile_id,
                    number=request.data.get("nonconformance_code") or f"NC-{order.order_number}-{str(inspection.id)[:8]}",
                    production_order=order,
                    inspection=inspection,
                    severity=request.data.get("severity", "medium"),
                    category=request.data.get("category", "process_quality"),
                    description=request.data.get("nonconformance_description", "Quality inspection requires review."),
                    owner_user_id=request.data.get("owner_user_id") or None,
                    created_by_user_id=get_request_user_id(request),
                    updated_by_user_id=get_request_user_id(request),
                )
            return Response(QualityInspectionSerializer(inspection).data, status=status.HTTP_201_CREATED)
        except ManufacturingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"], url_path=r"inspections/(?P<inspection_id>[^/.]+)/resolve")
    def resolve_inspection(self, request, pk=None, inspection_id=None):
        order = self.get_object()
        try:
            inspection = order.quality_inspections.get(pk=inspection_id)
            inspection = ManufacturingService.resolve_quality_inspection(
                inspection=inspection,
                status=request.data.get("status"),
                disposition=request.data.get("disposition", ""),
                released_quantity=request.data.get("released_quantity", 0),
                quarantined_quantity=request.data.get("quarantined_quantity", 0),
                rework_quantity=request.data.get("rework_quantity", 0),
                inspector_user_id=get_request_user_id(request),
                notes=request.data.get("notes", ""),
            )
            return Response(QualityInspectionSerializer(inspection).data)
        except (QualityInspection.DoesNotExist, ManufacturingError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class WorkCenterViewSet(ManufacturingScopedMixin, BaseCachePermissionViewset):
    queryset = WorkCenter.objects.all()
    serializer_class = WorkCenterSerializer
    filterset_fields = ["active", "code"]
    search_fields = ["code", "name"]
    CACHE_ENABLED = False
    required_permission = UNIFIED_PERMISSION_DICT["work_center"]

    def get_queryset(self):
        return scope_queryset_by_identity(super().get_queryset(), canonical_field="profile_id", legacy_field="profile", value=self.profile_id())

    def perform_create(self, serializer):
        serializer.save(profile_id=self.profile_id(), created_by_user_id=get_request_user_id(self.request))

    @action(detail=True, methods=["get", "post"], url_path="blocks")
    def blocks(self, request, pk=None):
        center = self.get_object()
        queryset = center.blocks.filter(active=True)
        if request.method == "GET":
            return Response(WorkCenterBlockSerializer(queryset, many=True).data)
        serializer = WorkCenterBlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(profile_id=self.profile_id(), work_center=center, created_by_user_id=get_request_user_id(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="capacity")
    def capacity(self, request, pk=None):
        center = self.get_object()
        start_value = request.query_params.get("date_from")
        end_value = request.query_params.get("date_to")
        try:
            start_date = date.fromisoformat(start_value) if start_value else timezone.localdate()
            end_date = date.fromisoformat(end_value) if end_value else start_date
        except ValueError:
            return Response({"detail": "date_from and date_to must use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        if end_date < start_date:
            return Response({"detail": "date_to must be on or after date_from."}, status=status.HTTP_400_BAD_REQUEST)

        range_start = timezone.make_aware(datetime.combine(start_date, time.min))
        range_end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
        days = (end_date - start_date).days + 1
        blocked_seconds = 0
        for block in center.blocks.filter(active=True, starts_at__lt=range_end, ends_at__gt=range_start):
            overlap_start = max(block.starts_at, range_start)
            overlap_end = min(block.ends_at, range_end)
            blocked_seconds += max(0, (overlap_end - overlap_start).total_seconds())

        planned_minutes = ProductionOperation.objects.filter(
            work_center=center,
            production_order__profile_id=self.profile_id(),
            production_order__created_at__date__range=(start_date, end_date),
        ).aggregate(total=Sum("planned_minutes"))["total"] or 0
        capacity_minutes = center.capacity_minutes_per_day * days
        blocked_minutes = round(blocked_seconds / 60)
        available_minutes = max(0, capacity_minutes - blocked_minutes)
        return Response({
            "work_center": str(center.id),
            "date_from": start_date,
            "date_to": end_date,
            "days": days,
            "capacity_minutes": capacity_minutes,
            "blocked_minutes": blocked_minutes,
            "available_minutes": available_minutes,
            "planned_minutes": planned_minutes,
            "utilization_percent": round((planned_minutes / available_minutes) * 100, 2) if available_minutes else 0,
        })


class CostingPeriodViewSet(ManufacturingScopedMixin, BaseCachePermissionViewset):
    queryset = CostingPeriod.objects.all()
    serializer_class = CostingPeriodSerializer
    filterset_fields = ["status", "period_start", "period_end"]
    CACHE_ENABLED = False
    required_permission = {
        "list": "view_profitability_reports",
        "retrieve": "view_profitability_reports",
        "create": "manage_accounting_periods",
        "update": "manage_accounting_periods",
        "partial_update": "manage_accounting_periods",
        "destroy": "manage_accounting_periods",
        "close": "manage_accounting_periods",
    }

    def get_queryset(self):
        return scope_queryset_by_identity(super().get_queryset(), canonical_field="profile_id", legacy_field="profile", value=self.profile_id())

    def perform_create(self, serializer):
        serializer.save(profile_id=self.profile_id(), created_by_user_id=get_request_user_id(self.request))

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        try:
            period = ManufacturingService.close_costing_period(
                period=self.get_object(),
                actor_user_id=get_request_user_id(request),
                notes=request.data.get("notes", ""),
            )
            return Response(CostingPeriodSerializer(period).data)
        except ManufacturingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class NonconformanceViewSet(ManufacturingScopedMixin, BaseCachePermissionViewset):
    queryset = Nonconformance.objects.select_related("production_order", "inspection")
    serializer_class = NonconformanceSerializer
    filterset_fields = ["status", "severity", "production_order"]
    search_fields = ["number", "category", "description"]
    required_permission = {
        "list": "read_manufacturing", "retrieve": "read_manufacturing",
        "create": "manage_quality", "update": "manage_quality",
        "partial_update": "manage_quality", "destroy": "manage_quality",
        "contain": "manage_quality", "investigate": "manage_quality", "close": "manage_quality",
    }

    def get_queryset(self):
        return scope_queryset_by_identity(super().get_queryset(), canonical_field="profile_id", legacy_field="profile", value=self.profile_id())

    def perform_create(self, serializer):
        serializer.save(profile_id=self.profile_id(), created_by_user_id=get_request_user_id(self.request))

    @action(detail=True, methods=["post"])
    def contain(self, request, pk=None):
        return self._transition(request, "contained")

    @action(detail=True, methods=["post"])
    def investigate(self, request, pk=None):
        return self._transition(request, "investigating")

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        return self._transition(request, "closed")

    def _transition(self, request, value):
        case = self.get_object()
        case.status = value
        case.updated_by_user_id = get_request_user_id(request)
        if value == "closed":
            case.closed_at = timezone.now()
            case.closed_by_user_id = get_request_user_id(request)
        case.save(update_fields=["status", "updated_by_user_id", "updated_at", "closed_at", "closed_by_user_id"])
        return Response(self.get_serializer(case).data)


class LotTraceabilityView(ManufacturingScopedMixin, APIView):
    def get(self, request, lot_id):
        try:
            lot = StockLot.objects.get(pk=lot_id, profile_id=self.profile_id())
            return Response(ManufacturingService.traceability_for_lot(profile_id=self.profile_id(), stock_lot=lot))
        except StockLot.DoesNotExist:
            return Response({"detail": "Stock lot not found."}, status=status.HTTP_404_NOT_FOUND)


class ProductionProfitabilityView(ManufacturingScopedMixin, APIView):
    permission_classes = [HasModelRequestPermission]
    required_permission = "view_profitability_reports"

    def get(self, request):
        profile_id = self.profile_id()
        orders = ProductionOrder.objects.filter(profile_id=profile_id).select_related("recipe", "finished_good")
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            orders = orders.filter(created_at__date__gte=date_from)
        if date_to:
            orders = orders.filter(created_at__date__lte=date_to)
        rows = []
        totals = {key: Decimal("0") for key in ("material_cost", "additional_cost", "waste_cost", "total_cost", "output_quantity")}
        for order in orders.order_by("-created_at")[:500]:
            summary = ManufacturingService.cost_summary(order)
            for key in totals:
                totals[key] += summary[key]
            rows.append({
                "production_order_id": str(order.id),
                "order_number": order.order_number,
                "finished_good_id": str(order.finished_good_id),
                "finished_good_name": order.finished_good.name_snapshot,
                "status": order.status,
                "costing_method": summary["costing_method"],
                "material_cost": summary["material_cost"],
                "additional_cost": summary["additional_cost"],
                "waste_cost": summary["waste_cost"],
                "total_cost": summary["total_cost"],
                "output_quantity": summary["output_quantity"],
                "unit_cost": summary["unit_cost"],
                "variance": summary["variance"],
                "allocation_valid": summary["allocation_valid"],
                "unallocated_cost": summary["unallocated_cost"],
            })
        totals["unit_cost"] = totals["total_cost"] / totals["output_quantity"] if totals["output_quantity"] else Decimal("0")
        return Response({"totals": totals, "orders": rows, "count": len(rows)})
