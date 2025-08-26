from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Sum, Count, Avg, F
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal



from mainapps.inventory.api.serializers import StockAnalyticsSerializer
from mainapps.inventory.api.views import BaseInventoryViewSet
from mainapps.inventory.models import Inventory
from mainapps.stock.api.serializers import StockItemDetailSerializer, StockItemListSerializer, StockLocationDetailSerializer, StockLocationListSerializer, StockLocationTypeSerializer
from mainapps.stock.models import StockItem, StockLocation, StockLocationType
from rest_framework import viewsets

from subapps.permissions.constants import UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import CachingMixin, PermissionRequiredMixin

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
        
        if not all([to_location_id, stock_item_id, quantity > 0]):
            return Response(
                {'error': 'to_location_id, stock_item_id, and quantity are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            stock_item = StockItem.objects.get(
                id=stock_item_id,
                location=from_location
            )
            
            if stock_item.quantity < quantity:
                return Response(
                    {'error': 'Insufficient stock quantity'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            to_location = StockLocation.objects.get(id=to_location_id)
            
            # Create new stock item in destination or update existing
            dest_stock_item, created = StockItem.objects.get_or_create(
                inventory=stock_item.inventory,
                location=to_location,
                defaults={
                    'name': stock_item.name,
                    'quantity': 0,
                    'purchase_price': stock_item.purchase_price
                }
            )
            
            # Update quantities
            stock_item.quantity = F('quantity') - quantity
            dest_stock_item.quantity = F('quantity') + quantity
            
            stock_item.save()
            dest_stock_item.save()
            
            # Create tracking records
            current_user_id = UserService.get_current_user(request).get('id')
            
            StockItemTracking.objects.create(
                inventory=stock_item.inventory,
                item=stock_item,
                tracking_type=31,  # LOCATION_CHANGE
                notes=f"Transferred {quantity} units to {to_location.name}",
                user=current_user_id
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
        except StockLocation.DoesNotExist:
            return Response(
                {'error': 'Destination location not found'},
                status=status.HTTP_404_NOT_FOUND
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
        profile_id = self.request.headers.get('X-Profile-ID')
        
        if profile_id:
            queryset = queryset.filter(inventory__profile=profile_id)
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
    # def get_serializer_class(self):
    #     if self.action == 'list':
    #         return StockItemListSerializer
    #     return StockItemDetailSerializer
    
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
        StockItemTracking.objects.create(
            inventory=stock_item.inventory,
            item=stock_item,
            tracking_type=60,  # STATUS_CHANGE
            notes=f"Status changed from {old_status} to {new_status}. Reason: {reason}",
            user=UserService.get_current_user(request).get('id'),
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
        
        inventory = Inventory.objects.get(
            external_system_id=inventory_id,
            profile=request.headers.get('X-Profile-ID')
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
        

    
    @action(detail=False, methods=['get'])
    def get_inventory_items(self,request, ):
        inventory_id = request.query_params.get('inventory_id')
        inventory = Inventory.objects.get(id=inventory_id)
        stock_items=StockItem.objects.filter(inventory=inventory)
        serializer = StockItemListSerializer(stock_items, many=True)
        return Response(serializer.data)
    

    @action(detail=True, methods=['get'])
    def tracking_history(self, request, pk=None):
        """Get complete tracking history for stock item"""
        stock_item = self.get_object()
        tracking = stock_item.tracking_info.all().order_by('-date')
        
        serializer = StockItemTrackingListSerializer(tracking, many=True)
        return Response(serializer.data)
    

    @action(detail=False, methods=['get'])
    def analytics(self, request):
        """Get stock analytics"""
        queryset = self.get_queryset()
        
        # Basic metrics
        total_items = queryset.count()
        total_locations = queryset.values('location').distinct().count()
        total_value = queryset.aggregate(
            total=Sum(F('quantity') * F('purchase_price'))
        )['total'] or Decimal('0.00')
        
        # Location distribution
        location_distribution = queryset.values(
            'location__name'
        ).annotate(
            item_count=Count('id'),
            total_quantity=Sum('quantity')
        ).order_by('-item_count')
        
        # Status distribution
        status_distribution = queryset.values('status').annotate(
            count=Count('id')
        )
        
        # Aging analysis (based on creation date)
        now = timezone.now().date()
        aging_analysis = {
            '0-30_days': queryset.filter(
                created_at__gte=now - timedelta(days=30)
            ).count(),
            '31-90_days': queryset.filter(
                created_at__gte=now - timedelta(days=90),
                created_at__lt=now - timedelta(days=30)
            ).count(),
            '91-365_days': queryset.filter(
                created_at__gte=now - timedelta(days=365),
                created_at__lt=now - timedelta(days=90)
            ).count(),
            'over_1_year': queryset.filter(
                created_at__lt=now - timedelta(days=365)
            ).count()
        }
        
        analytics_data = {
            'total_stock_items': total_items,
            'total_locations': total_locations,
            'total_stock_value': total_value,
            'location_distribution': list(location_distribution),
            'status_distribution': {item['status']: item['count'] for item in status_distribution},
            'aging_analysis': aging_analysis
        }
        
        serializer = StockAnalyticsSerializer(analytics_data)
        return Response(serializer.data)
    

