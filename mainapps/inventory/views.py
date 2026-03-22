from decimal import Decimal

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from mainapps.stock.models import StockLocation
from subapps.permissions.constants import UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset
from subapps.services.inventory_read_model import get_inventory_item_summary_map
from subapps.services.stock_domain import StockDomainError, StockDomainService
from subapps.utils.request_context import (
    get_request_profile_id,
    get_request_user_id,
    scope_queryset_by_identity,
)

from .models import InventoryCategory, InventoryItem
from .serializers import (
    InventoryCategoryDetailSerializer,
    InventoryCategoryListSerializer,
    InventoryDetailSerializer,
    InventoryListSerializer,
)


class BaseInventoryViewSet(BaseCachePermissionViewset):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    def get_queryset(self):
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        if profile_id:
            queryset = scope_queryset_by_identity(
                queryset,
                canonical_field='profile_id',
                legacy_field='profile',
                value=profile_id,
            )
        return queryset

    def perform_create(self, serializer):
        serializer.save(
            profile_id=get_request_profile_id(self.request, required=True, as_str=False),
            created_by_user_id=get_request_user_id(self.request, required=True, as_str=False),
            updated_by_user_id=get_request_user_id(self.request, as_str=False),
        )

    def perform_update(self, serializer):
        serializer.save(updated_by_user_id=get_request_user_id(self.request, as_str=False))


class InventoryCategoryViewSet(BaseInventoryViewSet):
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_category')
    queryset = InventoryCategory.objects.all()
    filterset_fields = ['is_active', 'structural', 'parent']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']
    serializer_class = InventoryCategoryDetailSerializer

    @action(detail=False, methods=['get'])
    def tree(self, request):
        categories = self.get_queryset().filter(parent__isnull=True)
        serializer = InventoryCategoryDetailSerializer(categories, many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def children(self, request, pk=None):
        category = self.get_object()
        serializer = InventoryCategoryListSerializer(category.children.all(), many=True, context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def items(self, request, pk=None):
        category = self.get_object()
        queryset = category.inventory_items.all().order_by('-created_at')
        summary_map = get_inventory_item_summary_map(list(queryset))
        serializer = InventoryListSerializer(
            queryset,
            many=True,
            context={**self.get_serializer_context(), 'inventory_item_summary_map': summary_map},
        )
        return Response(serializer.data)


class InventoryItemViewSet(BaseInventoryViewSet):
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_item')
    queryset = InventoryItem.objects.select_related('inventory_category', 'default_supplier')
    filterset_fields = ['status', 'inventory_type', 'inventory_category', 'default_supplier']
    search_fields = ['name_snapshot', 'description', 'sku_snapshot', 'barcode_snapshot']
    ordering_fields = ['name_snapshot', 'created_at', 'minimum_stock_level', 'reorder_point']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return InventoryListSerializer
        return InventoryDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        queryset = list(self.filter_queryset(self.get_queryset())) if self.action in {'list', 'low_stock', 'needs_reorder'} else []
        if self.action in {'retrieve', 'minimal_item', 'stock_summary'} and getattr(self, 'kwargs', {}).get('pk'):
            try:
                queryset = [self.get_object()]
            except Exception:
                queryset = []
        context['inventory_item_summary_map'] = get_inventory_item_summary_map(queryset) if queryset else {}
        return context

    def get_queryset(self):
        queryset = super().get_queryset()
        stock_status = self.request.query_params.get('stock_status')
        if stock_status:
            summary_map = get_inventory_item_summary_map(list(queryset))
            matching_ids = []
            for item in queryset:
                summary = summary_map.get(item.id, {})
                quantity = Decimal(str(summary.get('quantity', 0)))
                if stock_status == 'low_stock' and quantity <= Decimal(str(item.minimum_stock_level or 0)):
                    matching_ids.append(item.id)
                elif stock_status == 'needs_reorder' and quantity <= Decimal(str(item.reorder_point or 0)):
                    matching_ids.append(item.id)
                elif stock_status == 'out_of_stock' and quantity <= 0:
                    matching_ids.append(item.id)
            queryset = queryset.filter(id__in=matching_ids) if matching_ids else queryset.none()
        return queryset

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def needs_reorder(self, request):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def stock_summary(self, request, pk=None):
        inventory_item = self.get_object()
        summary = get_inventory_item_summary_map([inventory_item]).get(inventory_item.id, {})
        return Response({
            'total_quantity': summary.get('quantity', Decimal('0')),
            'quantity_reserved': summary.get('quantity_reserved', Decimal('0')),
            'quantity_available': summary.get('quantity_available', Decimal('0')),
            'total_locations': summary.get('location_count', 0),
            'avg_purchase_price': summary.get('avg_purchase_price', Decimal('0')),
            'total_value': summary.get('total_stock_value', Decimal('0')),
            'location_breakdown': summary.get('location_breakdown', []),
            'stock_status': summary.get('status', inventory_item.status),
            'expiry_date': summary.get('expiry_date'),
            'lot_count': summary.get('lot_count', 0),
            'serial_count': summary.get('serial_count', 0),
        })

    @action(detail=True, methods=['get'])
    def minimal_item(self, request, pk=None):
        serializer = InventoryListSerializer(self.get_object(), context=self.get_serializer_context())
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def adjust_stock(self, request, pk=None):
        inventory_item = self.get_object()
        location_id = request.data.get('location_id')
        quantity_change = request.data.get('quantity_change', 0)
        reason = request.data.get('reason', '')
        try:
            quantity_change = Decimal(str(quantity_change))
        except Exception:
            return Response({'error': 'quantity_change must be a valid number'}, status=status.HTTP_400_BAD_REQUEST)

        if not location_id or quantity_change == 0:
            return Response(
                {'error': 'location_id and quantity_change are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_id = get_request_profile_id(request, required=True, as_str=False)
        stock_location = scope_queryset_by_identity(
            StockLocation.objects.filter(id=location_id),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        ).first()
        if stock_location is None:
            return Response({'error': 'Stock location not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            adjustment_result = StockDomainService.adjust_stock(
                inventory_item=inventory_item,
                stock_location=stock_location,
                quantity_change=quantity_change,
                actor_user_id=get_request_user_id(request, as_str=False),
                reason=reason,
            )
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'message': 'Stock adjusted successfully',
            'old_quantity': adjustment_result['old_quantity'],
            'new_quantity': adjustment_result['new_quantity'],
            'change': quantity_change,
        })


InventoryViewSet = InventoryItemViewSet
