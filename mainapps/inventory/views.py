from rest_framework import viewsets, status, filters,generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Sum, Count, Avg, F
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal

from subapps.permissions.constants import UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset, PermissionRequiredMixin
from subapps.services.inventory_read_model import (
    get_inventory_ids_for_stock_filter,
    get_inventory_summary_map,
)
from subapps.services.stock_domain import StockDomainError, StockDomainService
from subapps.utils.request_context import (
    get_request_profile_id,
    get_request_user_id,
    scope_queryset_by_identity,
)
from mainapps.stock.models import StockLocation

from .models import (
    Inventory, InventoryCategory, InventoryBatch,
)
from .serializers import *


class BaseInventoryViewSet(BaseCachePermissionViewset):
    """Base viewset with common functionality"""
    # permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    
    def get_queryset(self):
        """Filter by profile (tenant)"""
        
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
        """Set created_by and profile on creation"""
        profile_id = get_request_profile_id(self.request, required=True, as_str=False)
        current_user_id = get_request_user_id(self.request, required=True, as_str=False)
        
        
        serializer.save(profile_id=profile_id, created_by_user_id=current_user_id)
    
    def perform_update(self, serializer):
        """Set modified_by on update"""
        current_user_id = get_request_user_id(self.request, as_str=False)

        extra_fields = {}
        if current_user_id:
            extra_fields['updated_by_user_id'] = current_user_id
        serializer.save(**extra_fields)

class InventoryCategoryViewSet(BaseInventoryViewSet):
    """ViewSet for inventory categories with hierarchical support"""
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_category')
    queryset = InventoryCategory.objects.all()
    filterset_fields = ['is_active', 'structural', 'parent']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']
    serializer_class=InventoryCategoryDetailSerializer
    
    # def get_serializer_class(self):
    #     if self.action == 'list':
    #         return InventoryCategoryListSerializer
    #     return InventoryCategoryDetailSerializer
    
    @action(detail=False, methods=['get'])
    def tree(self, request):
        """Get category tree structure"""
        categories = self.get_queryset().filter(parent__isnull=True)
        serializer = InventoryCategoryDetailSerializer(categories, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def children(self, request, pk=None):
        """Get direct children of a category"""
        category = self.get_object()
        children = category.children.all()
        serializer = InventoryCategoryListSerializer(children, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def inventories(self, request, pk=None):
        """Get all inventories in this category"""
        category = self.get_object()
        inventories = category.inventories.filter(active=True)
        serializer = InventoryListSerializer(inventories, many=True)
        return Response(serializer.data)

class InventoryViewSet(BaseInventoryViewSet):
    """Comprehensive ViewSet for inventory management"""
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory')

    queryset = Inventory.objects.select_related('category', 'default_supplier')
    filterset_fields = [
        'active', 'inventory_type', 'category', 'assembly', 'component',
        'trackable', 'purchaseable', 'salable', 'virtual'
    ]
    search_fields = ['name', 'description', 'IPN', 'external_system_id']
    ordering_fields = ['name', 'created_at', 'minimum_stock_level', 're_order_point']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return InventoryListSerializer
        return InventoryDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action in {'list', 'low_stock', 'needs_reorder'}:
            try:
                context['inventory_summary_map'] = get_inventory_summary_map(list(self.get_queryset()))
            except Exception:
                context['inventory_summary_map'] = {}
        elif self.action in {'retrieve', 'minimal_inventory', 'stock_summary'} and getattr(self, 'kwargs', {}).get('pk'):
            try:
                inventory = self.get_object()
                context['inventory_summary_map'] = get_inventory_summary_map([inventory])
            except Exception:
                context['inventory_summary_map'] = {}
        return context
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Additional filters
        stock_status = self.request.query_params.get('stock_status')
        if stock_status == 'low_stock':
            inventory_ids = get_inventory_ids_for_stock_filter(list(queryset), filter_name='low_stock')
            queryset = queryset.filter(id__in=inventory_ids) if inventory_ids else queryset.none()
        elif stock_status == 'out_of_stock':
            inventory_ids = get_inventory_ids_for_stock_filter(list(queryset), filter_name='out_of_stock')
            queryset = queryset.filter(id__in=inventory_ids) if inventory_ids else queryset.none()
        elif stock_status == 'needs_reorder':
            inventory_ids = get_inventory_ids_for_stock_filter(list(queryset), filter_name='needs_reorder')
            queryset = queryset.filter(id__in=inventory_ids) if inventory_ids else queryset.none()
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        """Get inventories with low stock"""
        queryset = self.get_queryset().low_stock()
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def needs_reorder(self, request):
        """Get inventories that need reordering"""
        queryset = self.get_queryset().needs_reorder()
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def stock_summary(self, request, pk=None):
        """Get stock summary for specific inventory"""
        inventory = self.get_object()
        summary = get_inventory_summary_map([inventory]).get(inventory.id, {})
        return Response({
            'total_quantity': summary.get('current_stock_level', Decimal('0')),
            'quantity_reserved': summary.get('quantity_reserved', Decimal('0')),
            'quantity_available': summary.get('quantity_available', Decimal('0')),
            'total_locations': summary.get('total_locations', 0),
            'avg_purchase_price': summary.get('avg_purchase_price', Decimal('0')),
            'total_value': summary.get('total_stock_value', Decimal('0')),
            'location_breakdown': summary.get('location_breakdown', []),
            'stock_status': summary.get('stock_status', inventory.stock_status),
            'expiring_lots': summary.get('expiring_lots', []),
        })
    @action(detail=True, methods=['get'])
    def minimal_inventory(self,request,pk=None):
        inventory = self.get_object()
        serializer = InventoryListSerializer(inventory)
        return Response(serializer.data)
        
    @action(detail=True, methods=['post'])
    def adjust_stock(self, request, pk=None):
        """Adjust stock levels for inventory"""
        inventory = self.get_object()
        adjustment_data = request.data
        
        # Validate adjustment data
        location_id = adjustment_data.get('location_id')
        quantity_change = adjustment_data.get('quantity_change', 0)
        reason = adjustment_data.get('reason', '')
        
        try:
            quantity_change = Decimal(str(quantity_change))
        except Exception:
            return Response(
                {'error': 'quantity_change must be a valid number'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not location_id or quantity_change == 0:
            return Response(
                {'error': 'location_id and quantity_change are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            profile_id = get_request_profile_id(request, required=True, as_str=False)
            current_user_id = get_request_user_id(request, as_str=False)
            stock_location = scope_queryset_by_identity(
                StockLocation.objects.filter(id=location_id),
                canonical_field='profile_id',
                legacy_field='profile',
                value=profile_id,
            ).first()
            if stock_location is None:
                return Response(
                    {'error': 'Stock location not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            adjustment_result = StockDomainService.adjust_stock(
                inventory=inventory,
                stock_location=stock_location,
                quantity_change=quantity_change,
                actor_user_id=current_user_id,
                reason=reason,
            )
            
            return Response({
                'message': 'Stock adjusted successfully',
                'old_quantity': adjustment_result['old_quantity'],
                'new_quantity': adjustment_result['new_quantity'],
                'change': quantity_change
            })
        except StockDomainError as exc:
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    

    @action(detail=False, methods=['get'])
    def analytics(self, request):
        """Get comprehensive inventory analytics"""
        queryset = list(self.get_queryset())
        
        # Basic counts
        total_inventories = len(queryset)
        active_inventories = sum(1 for inventory in queryset if inventory.active)

        summary_map = get_inventory_summary_map(queryset)
        low_stock_count = 0
        out_of_stock_count = 0
        total_stock_value = Decimal('0.00')
        category_accumulator = {}
        expiring_serialized = []

        for inventory in queryset:
            summary = summary_map.get(inventory.id, {})
            current_stock = Decimal(str(summary.get('current_stock_level', 0)))
            total_stock_value += Decimal(str(summary.get('total_stock_value', 0)))

            if current_stock <= 0:
                out_of_stock_count += 1
            elif current_stock <= Decimal(str(inventory.minimum_stock_level)):
                low_stock_count += 1

            category_name = inventory.category.name if inventory.category else 'Uncategorized'
            category_bucket = category_accumulator.setdefault(
                category_name,
                {'category__name': category_name, 'count': 0, 'total_value': Decimal('0')},
            )
            category_bucket['count'] += 1
            category_bucket['total_value'] += Decimal(str(summary.get('total_stock_value', 0)))

            for expiring_lot in summary.get('expiring_lots', []):
                expiring_serialized.append({
                    'inventory_name': inventory.name,
                    'lot_number': expiring_lot.get('lot_number', ''),
                    'expiry_date': expiring_lot.get('expiry_date'),
                    'quantity': expiring_lot.get('quantity', Decimal('0')),
                    'location_name': expiring_lot.get('location_name', ''),
                })

        stock_status_distribution = {
            'in_stock': max(total_inventories - low_stock_count - out_of_stock_count, 0),
            'low_stock': low_stock_count,
            'out_of_stock': out_of_stock_count
        }

        top_value_inventory_ids = [
            inventory.id
            for inventory in sorted(
                queryset,
                key=lambda inventory: Decimal(str(summary_map.get(inventory.id, {}).get('total_stock_value', 0))),
                reverse=True,
            )[:10]
        ]
        top_value_items = [inventory for inventory in queryset if inventory.id in top_value_inventory_ids]
        top_value_items.sort(
            key=lambda inventory: Decimal(str(summary_map.get(inventory.id, {}).get('total_stock_value', 0))),
            reverse=True,
        )
        top_value_serialized = InventoryListSerializer(
            top_value_items,
            many=True,
            context={'inventory_summary_map': summary_map},
        ).data
        category_breakdown = sorted(
            category_accumulator.values(),
            key=lambda row: row['count'],
            reverse=True,
        )
        expiring_serialized.sort(key=lambda row: row['expiry_date'] or timezone.now().date())
        
        analytics_data = {
            'total_inventories': total_inventories,
            'active_inventories': active_inventories,
            'low_stock_count': low_stock_count,
            'out_of_stock_count': out_of_stock_count,
            'total_stock_value': total_stock_value,
            'category_breakdown': category_breakdown,
            'stock_status_distribution': stock_status_distribution,
            'top_value_items': top_value_serialized,
            'expiring_soon': expiring_serialized[:10]
        }
        
        serializer = InventoryAnalyticsSerializer(analytics_data)
        return Response(serializer.data)
