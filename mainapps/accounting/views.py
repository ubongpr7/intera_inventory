from datetime import date

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from mainapps.orders.models import CustomerReturn, SalesOrderShipment
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset
from subapps.utils.request_context import get_request_profile_id, get_request_user_id, scope_queryset_by_identity

from .models import InventoryValuationSnapshot, JournalEntry
from .serializers import InventoryValuationSnapshotSerializer, JournalEntrySerializer
from .services import AccountingService


class JournalEntryViewSet(BaseCachePermissionViewset):
    queryset = JournalEntry.objects.prefetch_related('lines')
    serializer_class = JournalEntrySerializer
    http_method_names = ['get', 'head', 'options']
    required_permission = 'view_profitability_reports'

    def get_queryset(self):
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        if profile_id:
            queryset = scope_queryset_by_identity(queryset, canonical_field='profile_id', legacy_field='profile', value=profile_id)
        return queryset


class InventoryValuationSnapshotViewSet(BaseCachePermissionViewset):
    queryset = InventoryValuationSnapshot.objects.select_related('inventory_item', 'stock_location')
    serializer_class = InventoryValuationSnapshotSerializer
    http_method_names = ['get', 'post', 'head', 'options']
    required_permission = {
        'list': 'view_profitability_reports',
        'retrieve': 'view_profitability_reports',
        'create': 'manage_accounting_periods',
        'capture': 'manage_accounting_periods',
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        if profile_id:
            queryset = scope_queryset_by_identity(queryset, canonical_field='profile_id', legacy_field='profile', value=profile_id)
        snapshot_date = self.request.query_params.get('snapshot_date')
        if snapshot_date:
            queryset = queryset.filter(snapshot_date=snapshot_date)
        return queryset

    @action(detail=False, methods=['post'])
    def capture(self, request):
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        snapshot_date = request.data.get('snapshot_date') or date.today().isoformat()
        snapshots = AccountingService.capture_valuation(
            profile_id=profile_id,
            snapshot_date=date.fromisoformat(snapshot_date),
            costing_method=request.data.get('costing_method') or 'actual',
            actor_user_id=get_request_user_id(request, as_str=False),
        )
        return Response(InventoryValuationSnapshotSerializer(snapshots, many=True).data, status=status.HTTP_201_CREATED)
