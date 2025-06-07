from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()
router.register(r'locations', StockLocationViewSet, basename='stock-location')
router.register(r'stock-items', StockItemViewSet, basename='stock-item')

urlpatterns = [
    path('api/v1/', include(router.urls)),
]
