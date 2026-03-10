from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()

router.register(r'purchase-orders', PurchaseOrderViewSet, basename='purchase-order')
router.register(r'line-item', LineItemsViewset, basename='line-itemes')

urlpatterns = [
    path('', include(router.urls)), 
]
