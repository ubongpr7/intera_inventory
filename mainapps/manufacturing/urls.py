from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CostingPeriodViewSet, LotTraceabilityView, NonconformanceViewSet, ProductionOrderViewSet, ProductionProfitabilityView, RecipeViewSet, WorkCenterViewSet

router = DefaultRouter()
router.register("recipes", RecipeViewSet, basename="manufacturing-recipe")
router.register("production-orders", ProductionOrderViewSet, basename="production-order")
router.register("work-centers", WorkCenterViewSet, basename="work-center")
router.register("costing-periods", CostingPeriodViewSet, basename="costing-period")
router.register("nonconformances", NonconformanceViewSet, basename="nonconformance")

urlpatterns = [
    path("", include(router.urls)),
    path("traceability/lots/<uuid:lot_id>/", LotTraceabilityView.as_view(), name="lot-traceability"),
    path("reports/production-profitability/", ProductionProfitabilityView.as_view(), name="production-profitability"),
]
