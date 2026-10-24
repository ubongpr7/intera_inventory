from rest_framework import serializers

from .models import (
    MaterialIssue,
    ProductionCostEntry,
    ProductionOrder,
    ProductionOutput,
    Recipe,
    RecipeLine,
    RecipeOperation,
    RecipeOutput,
    WorkCenter,
    ProductionOperation,
    QualityInspection,
    MaterialReservation,
    MaterialRequirement,
    WasteRecord,
    CostingPeriod,
    Nonconformance,
    WorkCenterBlock,
)


class RecipeLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeLine
        fields = [
            "id", "component", "quantity", "uom_code", "scrap_percent",
            "line_type", "is_optional", "notes",
        ]
        read_only_fields = ["id"]


class RecipeOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeOutput
        fields = ["id", "output_item", "quantity", "uom_code", "allocation_percent", "is_primary", "notes"]
        read_only_fields = ["id"]


class WorkCenterSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkCenter
        fields = ["id", "code", "name", "active", "hourly_labor_rate", "hourly_overhead_rate", "capacity_minutes_per_day"]
        read_only_fields = ["id"]


class WorkCenterBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkCenterBlock
        fields = ["id", "work_center", "starts_at", "ends_at", "reason", "active", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        if attrs.get("ends_at") <= attrs.get("starts_at"):
            raise serializers.ValidationError("The block must end after it starts.")
        return attrs


class RecipeOperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecipeOperation
        fields = ["id", "sequence", "name", "work_center", "expected_minutes", "notes"]
        read_only_fields = ["id"]


class RecipeSerializer(serializers.ModelSerializer):
    lines = RecipeLineSerializer(many=True)
    outputs = RecipeOutputSerializer(many=True, required=False)
    operations = RecipeOperationSerializer(many=True, required=False)

    class Meta:
        model = Recipe
        fields = [
            "id", "name", "finished_good", "version", "status",
            "output_quantity", "output_uom_code", "target_yield_percent",
            "effective_from", "effective_until", "notes", "costing_method", "standard_unit_cost",
            "lines", "outputs", "operations",
        ]
        read_only_fields = ["id"]

    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        outputs = validated_data.pop("outputs", [])
        operations = validated_data.pop("operations", [])
        recipe = Recipe.objects.create(**validated_data)
        RecipeLine.objects.bulk_create([
            RecipeLine(profile_id=recipe.profile_id, recipe=recipe, **line)
            for line in lines
        ])
        RecipeOutput.objects.bulk_create([
            RecipeOutput(profile_id=recipe.profile_id, recipe=recipe, **output)
            for output in outputs
        ])
        RecipeOperation.objects.bulk_create([
            RecipeOperation(profile_id=recipe.profile_id, recipe=recipe, **operation)
            for operation in operations
        ])
        return recipe

    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        outputs = validated_data.pop("outputs", None)
        operations = validated_data.pop("operations", None)
        instance = super().update(instance, validated_data)
        if lines is not None:
            instance.lines.all().delete()
            RecipeLine.objects.bulk_create([
                RecipeLine(profile_id=instance.profile_id, recipe=instance, **line)
                for line in lines
            ])
        if outputs is not None:
            instance.outputs.all().delete()
            RecipeOutput.objects.bulk_create([
                RecipeOutput(profile_id=instance.profile_id, recipe=instance, **output)
                for output in outputs
            ])
        if operations is not None:
            instance.operations.all().delete()
            RecipeOperation.objects.bulk_create([
                RecipeOperation(profile_id=instance.profile_id, recipe=instance, **operation)
                for operation in operations
            ])
        return instance


class ProductionOrderSerializer(serializers.ModelSerializer):
    recipe_name = serializers.CharField(source="recipe.name", read_only=True)
    finished_good_name = serializers.CharField(source="finished_good.name_snapshot", read_only=True)
    material_issue_count = serializers.IntegerField(source="material_issues.count", read_only=True)
    output_count = serializers.IntegerField(source="outputs.count", read_only=True)

    class Meta:
        model = ProductionOrder
        fields = [
            "id", "order_number", "recipe", "recipe_name", "finished_good",
            "finished_good_name", "stock_location", "status", "planned_quantity",
            "actual_quantity", "scrap_quantity", "started_at", "completed_at",
            "notes", "costing_period", "material_issue_count", "output_count", "created_at", "idempotency_key",
        ]
        read_only_fields = [
            "id", "finished_good", "status", "actual_quantity", "scrap_quantity",
            "started_at", "completed_at", "material_issue_count", "output_count", "created_at", "idempotency_key",
        ]


class MaterialIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialIssue
        fields = [
            "id", "production_order", "recipe_line", "component", "stock_lot",
            "quantity", "quantity_uom_code", "unit_cost", "issued_at", "notes", "idempotency_key",
        ]
        read_only_fields = ["id", "component", "quantity_uom_code", "unit_cost", "issued_at"]


class ProductionCostEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionCostEntry
        fields = ["id", "production_order", "cost_type", "amount", "currency_code", "description"]
        read_only_fields = ["id", "production_order"]


class ProductionOutputSerializer(serializers.ModelSerializer):
    total_cost = serializers.ReadOnlyField()

    class Meta:
        model = ProductionOutput
        fields = [
            "id", "production_order", "output_item", "stock_lot", "quantity",
            "quantity_uom_code", "unit_cost", "total_cost", "produced_at", "notes", "idempotency_key",
            "output_kind", "cost_allocation_percent",
        ]
        read_only_fields = fields


class ProductionOperationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionOperation
        fields = [
            "id", "production_order", "recipe_operation", "sequence", "name", "work_center",
            "status", "planned_minutes", "actual_minutes", "started_at", "completed_at", "notes",
            "completed_quantity", "scrap_quantity",
        ]
        read_only_fields = ["id", "production_order", "sequence", "name", "work_center", "status", "started_at", "completed_at"]


class QualityInspectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QualityInspection
        fields = [
            "id", "production_order", "output", "status", "inspector_user_id",
            "inspected_quantity", "defect_quantity", "measurements", "notes", "inspected_at",
            "released_quantity", "quarantined_quantity", "rework_quantity", "disposition", "nonconformance_code",
        ]
        read_only_fields = ["id", "production_order", "status", "inspected_at"]


class MaterialReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaterialReservation
        fields = ["id", "production_order", "recipe_line", "stock_reservation", "quantity", "quantity_uom_code"]
        read_only_fields = fields


class MaterialRequirementSerializer(serializers.ModelSerializer):
    remaining_quantity = serializers.ReadOnlyField()

    class Meta:
        model = MaterialRequirement
        fields = [
            "id", "production_order", "recipe_line", "component", "required_quantity",
            "reserved_quantity", "issued_quantity", "remaining_quantity", "quantity_uom_code",
            "status", "notes",
        ]
        read_only_fields = fields


class WasteRecordSerializer(serializers.ModelSerializer):
    net_cost = serializers.ReadOnlyField()

    class Meta:
        model = WasteRecord
        fields = [
            "id", "production_order", "production_operation", "inventory_item", "source_stock_lot",
            "stock_movement", "recovered_stock_lot", "quantity", "quantity_uom_code", "unit_cost",
            "disposal_cost", "recovery_value", "net_cost", "waste_type", "disposition", "reason_code",
            "notes", "recorded_at", "idempotency_key",
        ]
        read_only_fields = ["id", "stock_movement", "recovered_stock_lot", "quantity_uom_code", "net_cost", "recorded_at"]


class CostingPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostingPeriod
        fields = ["id", "name", "period_start", "period_end", "status", "closed_at", "closed_by_user_id", "notes"]
        read_only_fields = ["id", "status", "closed_at", "closed_by_user_id"]


class NonconformanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Nonconformance
        fields = [
            "id", "number", "production_order", "inspection", "status", "severity", "category",
            "description", "root_cause", "containment_action", "corrective_action", "owner_user_id",
            "due_date", "closed_at", "closed_by_user_id", "closure_evidence", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "status", "closed_at", "closed_by_user_id", "created_at", "updated_at"]
