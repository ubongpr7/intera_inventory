from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import InventoryValuationSnapshotViewSet, JournalEntryViewSet

router = DefaultRouter()
router.register('journal-entries', JournalEntryViewSet, basename='journal-entry')
router.register('valuation-snapshots', InventoryValuationSnapshotViewSet, basename='valuation-snapshot')

urlpatterns = [path('', include(router.urls))]
