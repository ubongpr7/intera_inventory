from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()
router.register(r'location-types', ReadStockLocationType, basename='stock-location-type')
router.register(r'locations', StockLocationViewSet, basename='stock-location')
router.register(r'inventory-items', InventoryItemViewSet, basename='inventory-item')
router.register(r'reservations', StockReservationViewSet, basename='stock-reservation')


urlpatterns = [
    path('', include(router.urls)),
]
