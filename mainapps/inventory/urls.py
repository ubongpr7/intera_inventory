from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()
router.register(r'categories', InventoryCategoryViewSet, basename='inventory-category')
router.register(r'items', InventoryItemViewSet, basename='inventory-item')

urlpatterns = [
    path('', include(router.urls)),

]
