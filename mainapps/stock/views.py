from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Sum, Count, Avg, F
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal



from mainapps.inventory.serializers import StockAnalyticsSerializer
from mainapps.inventory.views import BaseInventoryViewSet
from mainapps.inventory.models import Inventory, InventoryItem
from mainapps.stock.serializers import (
    LowStockBalanceSerializer,
    LowStockItemSerializer,
    StockItemDetailSerializer,
    StockItemListSerializer,
    StockLocationDetailSerializer,
    StockLocationListSerializer,
    StockLocationTypeSerializer,
    StockReservationCreateSerializer,
    StockReservationMutationSerializer,
    StockReservationSerializer,
)
from mainapps.stock.models import StockItem, StockItemTracking, StockLocation, StockLocationType, StockLot, StockReservation

from subapps.permissions.constants import UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset, CachingMixin, PermissionRequiredMixin
from subapps.services.identity_directory import IdentityDirectory
from subapps.services.inventory_read_model import get_low_stock_rows, get_profile_stock_analytics
from subapps.services.stock_domain import StockDomainError, StockDomainService
from subapps.utils.request_context import get_request_profile_id, get_request_user_id, scope_queryset_by_identity

class ReadStockLocationType(viewsets.ReadOnlyModelViewSet):
    serializer_class= StockLocationTypeSerializer
    queryset = StockLocationType.objects.all()
class StockLocationViewSet(BaseInventoryViewSet):
    """ViewSet for stock location management"""
    required_permission = UNIFIED_PERMISSION_DICT.get('stock_location')

    queryset = StockLocation.objects.select_related('location_type', 'parent')
    filterset_fields = ['structural', 'external', 'location_type', 'parent']
    search_fields = ['name', 'code', 'description']
    ordering_fields = ['name', 'code', 'created_at']
    ordering = ['name']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return StockLocationListSerializer
        return StockLocationDetailSerializer
    
    @action(detail=True, methods=['get'])
    def stock_items(self, request, pk=None):
        """Get all stock items in this location"""
        location = self.get_object()
        stock_items = location.stock_items.all()
        
        # Apply filters
        status_filter = request.query_params.get('status')
        if status_filter:
            stock_items = stock_items.filter(status=status_filter)
        
        serializer = StockItemListSerializer(stock_items, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def transfer_stock(self, request, pk=None):
        """Transfer stock between locations"""
        from_location = self.get_object()
        transfer_data = request.data
        
        to_location_id = transfer_data.get('to_location_id')
        stock_item_id = transfer_data.get('stock_item_id')
        quantity = transfer_data.get('quantity', 0)
        try:
            quantity = Decimal(str(quantity))
        except Exception:
            return Response(
                {'error': 'quantity must be a valid number'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not all([to_location_id, stock_item_id, quantity > 0]):
            return Response(
                {'error': 'to_location_id, stock_item_id, and quantity are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            stock_item = self.get_queryset().get(
                id=stock_item_id,
                location=from_location
            )
            profile_id = get_request_profile_id(request, required=True, as_str=False)
            to_location = scope_queryset_by_identity(
                StockLocation.objects.filter(id=to_location_id),
                canonical_field='profile_id',
                legacy_field='profile',
                value=profile_id,
            ).first()
            if to_location is None:
                return Response(
                    {'error': 'Destination location not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

            StockDomainService.transfer_stock(
                stock_item=stock_item,
                to_location=to_location,
                quantity=quantity,
                actor_user_id=get_request_user_id(request, as_str=False),
            )
            
            return Response({
                'message': 'Stock transferred successfully',
                'transferred_quantity': quantity,
                'from_location': from_location.name,
                'to_location': to_location.name
            })
            
        except StockItem.DoesNotExist:
            return Response(
                {'error': 'Stock item not found in this location'},
                status=status.HTTP_404_NOT_FOUND
            )
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


class BaseInventoryViewSetMixin(CachingMixin,PermissionRequiredMixin,viewsets.ModelViewSet):
    """Base viewset with common functionality"""
    
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    
    def get_queryset(self):
        """Filter by profile (tenant)"""
        
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        
        if profile_id:
            queryset = scope_queryset_by_identity(
                queryset,
                canonical_field='inventory__profile_id',
                legacy_field='inventory__profile',
                value=profile_id,
            )
        return queryset
    

class StockItemViewSet(BaseInventoryViewSetMixin):
    """ViewSet for stock item management"""
    required_permission = UNIFIED_PERMISSION_DICT.get('stock_item')

    queryset = StockItem.objects.select_related('inventory', 'location', 'purchase_order')
    filterset_fields = ['status', 'location', 'inventory', 'purchase_order', 'sales_order']
    search_fields = ['name', 'sku', 'serial', 'batch','inventory']
    ordering_fields = ['name', 'quantity', 'expiry_date', 'created_at']
    ordering = ['-created_at']
    serializer_class=StockItemDetailSerializer
    def get_serializer_class(self):
        if self.action == 'list':
            return StockItemListSerializer
        return StockItemDetailSerializer
    
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context.update({
            "request": self.request,  
        })
        return context
    
    def get_queryset(self):
        queryset = super().get_queryset()
        inventory = self.request.query_params.get('inventory')
        product_variant = self.request.query_params.get('product_variant')
        
        if inventory:
            queryset = queryset.filter(inventory=inventory)
        if product_variant:
            queryset = queryset.filter(product_variant=product_variant)
               
        # Filter by expiry status
        expiry_filter = self.request.query_params.get('expiry_status')
        if expiry_filter == 'expired':
            queryset = queryset.filter(
                expiry_date__lt=timezone.now().date()
            )
        elif expiry_filter == 'expiring_soon':
            queryset = queryset.filter(
                expiry_date__lte=timezone.now().date() + timedelta(days=30),
                expiry_date__gt=timezone.now().date()
            )
        
        # Filter by quantity
        quantity_filter = self.request.query_params.get('quantity_filter')
        if quantity_filter == 'zero':
            queryset = queryset.filter(quantity=0)
        elif quantity_filter == 'low':
            queryset = queryset.filter(
                quantity__lte=F('inventory__minimum_stock_level')
            )
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def expiring_soon(self, request):
        """Get items expiring within specified days"""
        days = int(request.query_params.get('days', 30))
        cutoff_date = timezone.now().date() + timedelta(days=days)
        
        queryset = self.get_queryset().filter(
            expiry_date__lte=cutoff_date,
            expiry_date__gt=timezone.now().date()
        ).order_by('expiry_date')
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """Update stock item status"""
        stock_item = self.get_object()
        new_status = request.data.get('status')
        reason = request.data.get('reason', '')
        
        if not new_status:
            return Response(
                {'error': 'status is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        old_status = stock_item.status
        stock_item.status = new_status
        stock_item.save()
        
        # Create tracking record
        current_user = IdentityDirectory.get_current_user(request) or {}
        StockItemTracking.objects.create(
            inventory=stock_item.inventory,
            item=stock_item,
            tracking_type=60,  # STATUS_CHANGE
            notes=f"Status changed from {old_status} to {new_status}. Reason: {reason}",
            user=current_user.get('id'),
            deltas={
                'old_status': old_status,
                'new_status': new_status
            }
        )
        
        return Response({
            'message': 'Status updated successfully',
            'old_status': old_status,
            'new_status': new_status
        })
    @action(detail=False, methods=['POST'])
    def create_for_variants(self,request, ):
        """Create stock items for product variants"""
        data = request.data
        inventory_id = data.get('inventory')
        product_variant = data.get('product_variant', )
        
        if not inventory_id or not product_variant:
            return Response(
                {'error': 'inventory_id and product_variants are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        inventory = scope_queryset_by_identity(
            Inventory.objects.filter(external_system_id=inventory_id),
            canonical_field='profile_id',
            legacy_field='profile',
            value=get_request_profile_id(request, required=True, as_str=False),
        ).first()
        if inventory is None:
            return Response(
                {'error': 'Inventory not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
        
        stock_item,created = StockItem.objects.get_or_create(
            inventory=inventory,
            product_variant=product_variant,
            defaults={
                'name': request.data.get('name', ''),
                'quantity': 0,
                'delete_on_deplete': request.data.get('delete_on_deplete', False),
            }
        )
        serializer = self.get_serializer(stock_item)
        return Response(serializer.data, status=status.HTTP_200_OK)
        

    
    @action(detail=False, methods=['get'], url_path='inventory-items', url_name='inventory-items')
    def get_inventory_items(self,request, ):
        inventory_id = request.query_params.get('inventory_id')
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        inventory = scope_queryset_by_identity(
            Inventory.objects.filter(id=inventory_id),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        ).first()
        if inventory is None:
            return Response(
                {'error': 'Inventory not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        stock_items = self.get_queryset().filter(inventory=inventory)
        serializer = StockItemListSerializer(stock_items, many=True)
        return Response(serializer.data)
    

    @action(detail=True, methods=['get'])
    def tracking_history(self, request, pk=None):
        """Get complete tracking history for stock item"""
        stock_item = self.get_object()
        tracking = stock_item.tracking_info.all().order_by('-date')
        
        serializer = StockItemTrackingListSerializer(tracking, many=True,context={'request': request})
        return Response(serializer.data)


    @action(detail=False, methods=['get'])
    def analytics(self, request):
        """Get stock analytics"""
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        analytics_data = get_profile_stock_analytics(profile_id=profile_id)
        serializer = StockAnalyticsSerializer(analytics_data,context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        """Get StockItems with low quantity for dashboard view."""
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        inventories = scope_queryset_by_identity(
            Inventory.objects.all(),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        )
        rows = get_low_stock_rows(inventories)

        page = self.paginate_queryset(rows)
        if page is not None:
            serializer = LowStockBalanceSerializer(page, many=True,context={'request': request})
            return self.get_paginated_response(serializer.data)

        serializer = LowStockBalanceSerializer(rows, many=True,context={'request': request})
        return Response(serializer.data)


class StockReservationViewSet(BaseCachePermissionViewset):
    required_permission = UNIFIED_PERMISSION_DICT.get('stock_reservation')
    queryset = StockReservation.objects.select_related('inventory_item', 'stock_location', 'stock_lot')
    filterset_fields = ['status', 'stock_location', 'external_order_type', 'external_order_id']
    search_fields = ['external_order_id', 'external_order_line_id']
    ordering_fields = ['created_at', 'expires_at', 'status']
    ordering = ['-created_at']
    http_method_names = ['get', 'post', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'create':
            return StockReservationCreateSerializer
        if self.action in {'release', 'fulfill'}:
            return StockReservationMutationSerializer
        return StockReservationSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        if profile_id:
            queryset = queryset.filter(profile_id=profile_id)

        inventory_id = self.request.query_params.get('inventory')
        if inventory_id:
            queryset = queryset.filter(inventory_item_id=InventoryItem.legacy_bridge_id(inventory_id))
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        profile_id = get_request_profile_id(request, required=True, as_str=False)
        inventory = scope_queryset_by_identity(
            Inventory.objects.filter(id=data['inventory_id']),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        ).first()
        if inventory is None:
            return Response({'error': 'Inventory not found'}, status=status.HTTP_404_NOT_FOUND)

        stock_location = scope_queryset_by_identity(
            StockLocation.objects.filter(id=data['location_id']),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        ).first()
        if stock_location is None:
            return Response({'error': 'Stock location not found'}, status=status.HTTP_404_NOT_FOUND)

        stock_lot = None
        stock_lot_id = data.get('stock_lot_id')
        if stock_lot_id:
            stock_lot = StockLot.objects.filter(profile_id=profile_id, id=stock_lot_id).first()
            if stock_lot is None:
                return Response({'error': 'Stock lot not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = StockDomainService.reserve_stock(
                inventory=inventory,
                stock_location=stock_location,
                quantity=data['quantity'],
                external_order_type=data['external_order_type'],
                external_order_id=data['external_order_id'],
                external_order_line_id=data.get('external_order_line_id', ''),
                stock_lot=stock_lot,
                expires_at=data.get('expires_at'),
                actor_user_id=get_request_user_id(request, as_str=False),
                notes=data.get('notes', ''),
            )
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        output = StockReservationSerializer(result['reservation'], context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        reservation = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = StockDomainService.release_reservation(
                reservation=reservation,
                quantity=serializer.validated_data.get('quantity'),
                actor_user_id=get_request_user_id(request, as_str=False),
                notes=serializer.validated_data.get('notes', ''),
            )
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        output = StockReservationSerializer(result['reservation'], context=self.get_serializer_context())
        return Response(output.data)

    @action(detail=True, methods=['post'])
    def fulfill(self, request, pk=None):
        reservation = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = StockDomainService.fulfill_reservation(
                reservation=reservation,
                quantity=serializer.validated_data.get('quantity'),
                actor_user_id=get_request_user_id(request, as_str=False),
                notes=serializer.validated_data.get('notes', ''),
            )
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        output = StockReservationSerializer(result['reservation'], context=self.get_serializer_context())
        return Response(output.data)
    
