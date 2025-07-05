from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    POSConfigurationViewSet, POSTerminalViewSet, CustomerViewSet,
    TableViewSet, POSSessionViewSet, POSOrderViewSet,
    POSProductViewSet, POSDiscountViewSet, POSAnalyticsViewSet
)

router = DefaultRouter()
router.register(r'configurations', POSConfigurationViewSet)
router.register(r'terminals', POSTerminalViewSet)
router.register(r'customers', CustomerViewSet)
router.register(r'tables', TableViewSet)
router.register(r'sessions', POSSessionViewSet)
router.register(r'orders', POSOrderViewSet)
router.register(r'products', POSProductViewSet, basename='pos-products')
router.register(r'discounts', POSDiscountViewSet)
router.register(r'analytics', POSAnalyticsViewSet, basename='pos-analytics')

urlpatterns = [
    path('', include(router.urls)),
]
