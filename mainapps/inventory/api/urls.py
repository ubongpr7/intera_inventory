from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()
router.register(r'categories', InventoryCategoryViewSet, basename='inventory-category')
router.register(r'inventories', InventoryViewSet, basename='inventory')

urlpatterns = [
    path('api/v1/', include(router.urls)),
]
