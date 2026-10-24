from django.contrib import admin

from .models import (
    MaterialIssue,
    ProductionCostEntry,
    ProductionOperation,
    ProductionOrder,
    ProductionOutput,
    QualityInspection,
    Recipe,
    RecipeLine,
    RecipeOperation,
    RecipeOutput,
    WorkCenter,
    Nonconformance,
    WorkCenterBlock,
)


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("name", "finished_good", "version", "status", "costing_method")
    list_filter = ("status", "costing_method")
    search_fields = ("name", "finished_good__name_snapshot")


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "finished_good", "status", "planned_quantity", "actual_quantity", "created_at")
    list_filter = ("status",)
    search_fields = ("order_number", "finished_good__name_snapshot")


@admin.register(WorkCenter)
class WorkCenterAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "active", "capacity_minutes_per_day")
    list_filter = ("active",)
    search_fields = ("code", "name")


admin.site.register((RecipeLine, RecipeOutput, RecipeOperation, MaterialIssue, ProductionOutput, ProductionCostEntry, ProductionOperation, QualityInspection, Nonconformance, WorkCenterBlock))
