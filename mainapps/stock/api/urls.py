from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()
router.register(r'location-types', ReadStockLocationType, basename='stock-location-type')
router.register(r'locations', StockLocationViewSet, basename='stock-location')
router.register(r'stock-items', StockItemViewSet, basename='stock-item')


urlpatterns = [
    path('', include(router.urls)),
    path('stock-items/get_inventory_items<str:inventory_id>/',StockItemViewSet.get_inventory_items,name='inventory_items' )
]
