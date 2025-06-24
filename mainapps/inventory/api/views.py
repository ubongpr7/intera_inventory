from rest_framework import viewsets, status, filters,generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Sum, Count, Avg, F
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal

from subapps.services.user_service import UserService

from ..models import (
    Inventory, InventoryCategory, InventoryBatch,
)
from .serializers import *

class UnitListView(generics.ListAPIView):
    queryset = Unit.objects.all()
    serializer_class = UnitSerializer

class BaseInventoryViewSet(viewsets.ModelViewSet):
    """Base viewset with common functionality"""
    # permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    
    def get_queryset(self):
        """Filter by profile (tenant)"""
        
        queryset = super().get_queryset()
        profile_id = self.request.headers.get('X-Profile-ID')
        print('profile_id ', profile_id)
        if profile_id:
            queryset = queryset.filter(profile=profile_id)
        return queryset
    
    def perform_create(self, serializer):
        """Set created_by and profile on creation"""
        # current_user_id= self.request.user.id
        profile_id = str(self.request.headers.get('X-Profile-ID'))
        current_user_id= str(self.request.user.id)
        if not serializer.is_valid:
            print('Invalid')
        else:
            print('valid')
        
        serializer.save(profile = profile_id, created_by=current_user_id)
    
    def perform_update(self, serializer):
        """Set modified_by on update"""
        # current_user_id= self.request.user.id
        current_user_id= self.request.user.id

        extra_fields = {}
        if current_user_id:
            extra_fields['modified_by'] = current_user_id
        serializer.save(**extra_fields)

class InventoryCategoryViewSet(BaseInventoryViewSet):
    """ViewSet for inventory categories with hierarchical support"""
    queryset = InventoryCategory.objects.all()
    filterset_fields = ['is_active', 'structural', 'parent']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return InventoryCategoryListSerializer
        return InventoryCategoryDetailSerializer
    
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
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Additional filters
        stock_status = self.request.query_params.get('stock_status')
        if stock_status == 'low_stock':
            queryset = queryset.filter(
                stock_items__quantity__lte=F('minimum_stock_level')
            ).distinct()
        elif stock_status == 'out_of_stock':
            queryset = queryset.filter(
                Q(stock_items__isnull=True) | Q(stock_items__quantity=0)
            ).distinct()
        elif stock_status == 'needs_reorder':
            queryset = queryset.filter(
                stock_items__quantity__lte=F('re_order_point')
            ).distinct()
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        """Get inventories with low stock"""
        queryset = self.get_queryset().filter(
            stock_items__quantity__lte=F('minimum_stock_level')
        ).distinct()
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def needs_reorder(self, request):
        """Get inventories that need reordering"""
        queryset = self.get_queryset().filter(
            stock_items__quantity__lte=F('re_order_point')
        ).distinct()
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def stock_summary(self, request, pk=None):
        """Get stock summary for specific inventory"""
        inventory = self.get_object()
        stock_items = inventory.stock_items.all()
        
        summary = stock_items.aggregate(
            total_quantity=Sum('quantity'),
            total_locations=Count('location', distinct=True),
            avg_purchase_price=Avg('purchase_price'),
            total_value=Sum(F('quantity') * F('purchase_price'))
        )
        
        # Add location breakdown
        location_breakdown = stock_items.values(
            'location__name'
        ).annotate(
            quantity=Sum('quantity')
        ).order_by('-quantity')
        
        summary['location_breakdown'] = list(location_breakdown)
        summary['stock_status'] = inventory.stock_status
        
        return Response(summary)
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
        
        if not location_id or quantity_change == 0:
            return Response(
                {'error': 'location_id and quantity_change are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Find or create stock item
            stock_item, created = StockItem.objects.get_or_create(
                inventory=inventory,
                location_id=location_id,
                defaults={
                    'name': inventory.name,
                    'quantity': 0
                }
            )
            
            old_quantity = stock_item.quantity
            stock_item.quantity = F('quantity') + quantity_change
            stock_item.save()
            stock_item.refresh_from_db()
            
            # Create tracking record
            StockItemTracking.objects.create(
                inventory=inventory,
                item=stock_item,
                tracking_type=30,  # STOCK_ADJUSTMENT
                notes=f"Manual adjustment: {reason}",
                user=UserService.get_current_user(request).get('id'),
                deltas={
                    'quantity_before': float(old_quantity),
                    'quantity_after': float(stock_item.quantity),
                    'quantity_change': float(quantity_change)
                }
            )
            
            return Response({
                'message': 'Stock adjusted successfully',
                'old_quantity': old_quantity,
                'new_quantity': stock_item.quantity,
                'change': quantity_change
            })
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    

    @action(detail=False, methods=['get'])
    def analytics(self, request):
        """Get comprehensive inventory analytics"""
        queryset = self.get_queryset()
        
        # Basic counts
        total_inventories = queryset.count()
        active_inventories = queryset.filter(active=True).count()
        
        # Stock status analysis
        low_stock_count = queryset.filter(
            stock_items__quantity__lte=F('minimum_stock_level')
        ).distinct().count()
        
        out_of_stock_count = queryset.filter(
            Q(stock_items__isnull=True) | Q(stock_items__quantity=0)
        ).distinct().count()
        
        # Value analysis
        total_stock_value = StockItem.objects.filter(
            inventory__in=queryset
        ).aggregate(
            total=Sum(F('quantity') * F('purchase_price'))
        )['total'] or Decimal('0.00')
        
        # Category breakdown
        category_breakdown = queryset.values(
            'category__name'
        ).annotate(
            count=Count('id'),
            total_value=Sum(F('stock_items__quantity') * F('stock_items__purchase_price'))
        ).order_by('-count')
        
        # Stock status distribution
        stock_status_distribution = {
            'in_stock': total_inventories - low_stock_count - out_of_stock_count,
            'low_stock': low_stock_count,
            'out_of_stock': out_of_stock_count
        }
        
        # Top value items
        top_value_items = queryset.annotate(
            total_value=Sum(F('stock_items__quantity') * F('stock_items__purchase_price'))
        ).order_by('-total_value')[:10]
        
        top_value_serialized = InventoryListSerializer(top_value_items, many=True).data
        
        # Expiring soon
        expiring_soon = StockItem.objects.filter(
            inventory__in=queryset,
            expiry_date__lte=timezone.now().date() + timedelta(days=30),
            expiry_date__isnull=False
        ).select_related('inventory')[:10]
        
        expiring_serialized = StockItemListSerializer(expiring_soon, many=True).data
        
        analytics_data = {
            'total_inventories': total_inventories,
            'active_inventories': active_inventories,
            'low_stock_count': low_stock_count,
            'out_of_stock_count': out_of_stock_count,
            'total_stock_value': total_stock_value,
            'category_breakdown': list(category_breakdown),
            'stock_status_distribution': stock_status_distribution,
            'top_value_items': top_value_serialized,
            'expiring_soon': expiring_serialized
        }
        
        serializer = InventoryAnalyticsSerializer(analytics_data)
        return Response(serializer.data)
