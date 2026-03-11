from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
import uuid
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import F
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal

from mainapps.inventory.serializers import StockAnalyticsSerializer
from mainapps.inventory.views import BaseInventoryViewSet
from mainapps.inventory.models import Inventory, InventoryItem
from mainapps.projections.models import CatalogVariantProjection
from mainapps.stock.serializers import (
    LowStockBalanceSerializer,
    StockMovementListSerializer,
    StockItemDetailSerializer,
    StockItemListSerializer,
    StockLocationDetailSerializer,
    StockLocationListSerializer,
    StockLocationTypeSerializer,
    StockReservationCreateSerializer,
    StockReservationMutationSerializer,
    StockReservationSerializer,
)
from mainapps.stock.models import (
    StockItem,
    StockItemTracking,
    StockLocation,
    StockLocationType,
    StockLot,
    StockMovement,
    StockReservation,
    StockSerial,
)

from subapps.permissions.constants import UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset, CachingMixin, PermissionRequiredMixin
from subapps.services.inventory_read_model import (
    get_inventory_item_summary_map,
    get_low_stock_rows,
    get_profile_stock_analytics,
)
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
        inventory_item_ids = set(
            location.stock_balances.values_list('inventory_item_id', flat=True)
        )
        inventory_item_ids.update(
            location.stock_items.exclude(inventory_item_id__isnull=True).values_list('inventory_item_id', flat=True)
        )
        inventory_item_ids.update(
            InventoryItem.legacy_bridge_id(inventory_id)
            for inventory_id in location.stock_items.filter(inventory_id__isnull=False).values_list('inventory_id', flat=True)
        )
        stock_items = InventoryItem.objects.filter(id__in=inventory_item_ids).order_by('-created_at')
        summary_map = get_inventory_item_summary_map(stock_items, stock_location=location)

        status_filter = request.query_params.get('status')
        if status_filter:
            matching_ids = [
                item.id
                for item in stock_items
                if summary_map.get(item.id, {}).get('status') == status_filter or item.status == status_filter
            ]
            stock_items = stock_items.filter(id__in=matching_ids)
            summary_map = {item_id: summary for item_id, summary in summary_map.items() if item_id in matching_ids}

        serializer = StockItemListSerializer(
            stock_items,
            many=True,
            context={'request': request, 'inventory_item_summary_map': summary_map},
        )
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def transfer_stock(self, request, pk=None):
        """Transfer stock between locations"""
        from_location = self.get_object()
        transfer_data = request.data
        
        to_location_id = transfer_data.get('to_location_id')
        stock_item_id = transfer_data.get('stock_item_id')
        inventory_item_id = transfer_data.get('inventory_item_id')
        stock_lot_id = transfer_data.get('stock_lot_id')
        stock_serial_id = transfer_data.get('stock_serial_id')
        serial_number = transfer_data.get('serial_number', '')
        quantity = transfer_data.get('quantity', 0)
        try:
            quantity = Decimal(str(quantity))
        except Exception:
            return Response(
                {'error': 'quantity must be a valid number'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not all([to_location_id, quantity > 0]) or not (stock_item_id or inventory_item_id):
            return Response(
                {'error': 'to_location_id, quantity, and either stock_item_id or inventory_item_id are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            stock_item = None
            inventory_item = None
            stock_lot = None
            stock_serial = None

            if stock_item_id:
                stock_item = scope_queryset_by_identity(
                    StockItem.objects.filter(id=stock_item_id, location=from_location),
                    canonical_field='inventory__profile_id',
                    legacy_field='inventory__profile',
                    value=get_request_profile_id(request, required=True, as_str=False),
                ).select_related('inventory', 'inventory_item').first()
                if stock_item is None:
                    return Response(
                        {'error': 'Stock item not found in this location'},
                        status=status.HTTP_404_NOT_FOUND
                    )
                inventory_item = stock_item.inventory_item
                if inventory_item is None and (stock_lot_id or stock_serial_id or serial_number):
                    inventory_item = StockDomainService.ensure_inventory_item(
                        stock_item=stock_item,
                        actor_user_id=get_request_user_id(request, as_str=False),
                    )
            else:
                inventory_item = scope_queryset_by_identity(
                    InventoryItem.objects.filter(id=inventory_item_id),
                    canonical_field='profile_id',
                    legacy_field='profile',
                    value=get_request_profile_id(request, required=True, as_str=False),
                ).first()
                if inventory_item is None:
                    return Response(
                        {'error': 'Inventory item not found'},
                        status=status.HTTP_404_NOT_FOUND
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

            if stock_lot_id:
                stock_lot = StockLot.objects.filter(
                    id=stock_lot_id,
                    profile_id=profile_id,
                    inventory_item=inventory_item,
                ).first()
                if stock_lot is None:
                    return Response(
                        {'error': 'Stock lot not found for the selected inventory item'},
                        status=status.HTTP_404_NOT_FOUND
                    )

            if stock_serial_id:
                stock_serial = StockSerial.objects.filter(
                    id=stock_serial_id,
                    profile_id=profile_id,
                    inventory_item=inventory_item,
                ).first()
                if stock_serial is None:
                    return Response(
                        {'error': 'Stock serial not found for the selected inventory item'},
                        status=status.HTTP_404_NOT_FOUND
                    )

            StockDomainService.transfer_stock(
                stock_item=stock_item,
                inventory_item=inventory_item,
                from_location=from_location,
                to_location=to_location,
                quantity=quantity,
                actor_user_id=get_request_user_id(request, as_str=False),
                stock_lot=stock_lot,
                stock_serial=stock_serial,
                serial_number=serial_number,
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
    profile_scope_field = 'inventory__profile_id'
    legacy_profile_scope_field = 'inventory__profile'
    
    def get_queryset(self):
        """Filter by profile (tenant)"""
        
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        
        if profile_id:
            queryset = scope_queryset_by_identity(
                queryset,
                canonical_field=self.profile_scope_field,
                legacy_field=self.legacy_profile_scope_field,
                value=profile_id,
            )
        return queryset
    

class StockItemViewSet(BaseInventoryViewSetMixin):
    """Compatibility stock API now backed by InventoryItem and stock balances."""
    required_permission = UNIFIED_PERMISSION_DICT.get('stock_item')
    profile_scope_field = 'profile_id'
    legacy_profile_scope_field = 'profile'
    queryset = InventoryItem.objects.select_related('inventory_category', 'default_supplier')
    filterset_fields = ['status', 'inventory_category', 'inventory_type', 'default_supplier']
    search_fields = ['name_snapshot', 'sku_snapshot', 'barcode_snapshot']
    ordering_fields = ['name_snapshot', 'created_at', 'minimum_stock_level', 'reorder_point']
    ordering = ['-created_at']
    serializer_class = StockItemDetailSerializer

    def get_serializer_class(self):
        if self.action == 'list':
            return StockItemListSerializer
        return StockItemDetailSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context.update({"request": self.request})
        if self.action in {'list', 'retrieve', 'expiring_soon', 'get_inventory_items'}:
            try:
                context['inventory_item_summary_map'] = get_inventory_item_summary_map(list(self.get_queryset()))
            except Exception:
                context['inventory_item_summary_map'] = {}
        return context

    def get_queryset(self):
        queryset = super().get_queryset()
        inventory = self.request.query_params.get('inventory')
        location = self.request.query_params.get('location')
        purchase_order = self.request.query_params.get('purchase_order')
        sales_order = self.request.query_params.get('sales_order')
        product_variant = self.request.query_params.get('product_variant')

        if inventory:
            queryset = queryset.filter(
                Q(id=InventoryItem.legacy_bridge_id(inventory)) |
                Q(metadata__legacy_inventory_id=str(inventory))
            )
        if location:
            queryset = queryset.filter(
                Q(stock_balances__stock_location_id=location) |
                Q(legacy_stock_items__location_id=location)
            ).distinct()
        if purchase_order:
            queryset = queryset.filter(legacy_stock_items__purchase_order_id=purchase_order).distinct()
        if sales_order:
            queryset = queryset.filter(legacy_stock_items__sales_order_id=sales_order).distinct()
        if product_variant:
            queryset = queryset.filter(
                Q(barcode_snapshot=product_variant) |
                Q(product_variant_id=product_variant)
            )

        expiry_filter = self.request.query_params.get('expiry_status')
        if expiry_filter == 'expired':
            queryset = queryset.filter(stock_lots__expiry_date__lt=timezone.now().date()).distinct()
        elif expiry_filter == 'expiring_soon':
            queryset = queryset.filter(
                stock_lots__expiry_date__lte=timezone.now().date() + timedelta(days=30),
                stock_lots__expiry_date__gt=timezone.now().date()
            ).distinct()

        quantity_filter = self.request.query_params.get('quantity_filter')
        if quantity_filter in {'zero', 'low'}:
            summary_map = get_inventory_item_summary_map(list(queryset))
            matching_ids = []
            for item in queryset:
                quantity = summary_map.get(item.id, {}).get('quantity', Decimal('0'))
                if quantity_filter == 'zero' and quantity <= 0:
                    matching_ids.append(item.id)
                elif quantity_filter == 'low' and quantity <= Decimal(str(item.minimum_stock_level or 0)):
                    matching_ids.append(item.id)
            queryset = queryset.filter(id__in=matching_ids) if matching_ids else queryset.none()

        return queryset

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_value = self.kwargs.get(self.lookup_field or 'pk')
        inventory_item = queryset.filter(id=lookup_value).first()
        if inventory_item is not None:
            self.check_object_permissions(self.request, inventory_item)
            return inventory_item

        legacy_stock_item = scope_queryset_by_identity(
            StockItem.objects.filter(id=lookup_value).select_related('inventory', 'inventory_item'),
            canonical_field='inventory__profile_id',
            legacy_field='inventory__profile',
            value=get_request_profile_id(self.request, required=True, as_str=False),
        ).first()
        if legacy_stock_item is None:
            return super().get_object()

        inventory_item = legacy_stock_item.inventory_item
        if inventory_item is None and legacy_stock_item.inventory_id:
            bridge_id = InventoryItem.legacy_bridge_id(legacy_stock_item.inventory_id)
            inventory_item = queryset.filter(id=bridge_id).first()
        if inventory_item is None:
            return super().get_object()

        self.check_object_permissions(self.request, inventory_item)
        return inventory_item

    @action(detail=False, methods=['get'])
    def expiring_soon(self, request):
        """Get items expiring within specified days"""
        days = int(request.query_params.get('days', 30))
        cutoff_date = timezone.now().date() + timedelta(days=days)

        queryset = self.get_queryset().filter(
            stock_lots__expiry_date__lte=cutoff_date,
            stock_lots__expiry_date__gt=timezone.now().date()
        ).distinct().order_by('stock_lots__expiry_date')

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """Update inventory item lifecycle status."""
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
        stock_item.updated_by_user_id = get_request_user_id(request, as_str=False)
        stock_item.save(update_fields=['status', 'updated_by_user_id', 'updated_at'])

        StockMovement.objects.create(
            profile_id=stock_item.profile_id,
            inventory_item=stock_item,
            movement_type='adjustment',
            quantity=Decimal('0'),
            reference_type='inventory_item_status',
            reference_id=str(stock_item.id),
            actor_user_id=get_request_user_id(request, as_str=False),
            notes=f"Status changed from {old_status} to {new_status}. Reason: {reason}",
            created_by_user_id=get_request_user_id(request, as_str=False),
            updated_by_user_id=get_request_user_id(request, as_str=False),
        )

        return Response({
            'message': 'Status updated successfully',
            'old_status': old_status,
            'new_status': new_status
        })

    @action(detail=False, methods=['POST'])
    def create_for_variants(self,request, ):
        """Create or hydrate inventory items for catalog variants."""
        data = request.data
        inventory_id = data.get('inventory')
        product_variant = data.get('product_variant', )

        if not inventory_id or not product_variant:
            return Response(
                {'error': 'inventory_id and product_variants are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        inventory_filter = Q(external_system_id=inventory_id)
        try:
            inventory_filter |= Q(id=uuid.UUID(str(inventory_id)))
        except (TypeError, ValueError, AttributeError):
            pass
        inventory = scope_queryset_by_identity(
            Inventory.objects.filter(inventory_filter),
            canonical_field='profile_id',
            legacy_field='profile',
            value=get_request_profile_id(request, required=True, as_str=False),
        ).first()
        if inventory is None:
            return Response(
                {'error': 'Inventory not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        profile_id = get_request_profile_id(request, required=True, as_str=False)
        variant_queryset = CatalogVariantProjection.objects.select_related('product').filter(
            profile_id=profile_id,
        )
        try:
            variant_uuid = uuid.UUID(str(product_variant))
        except (TypeError, ValueError, AttributeError):
            variant_uuid = None
        variant_filter = Q(variant_barcode=product_variant)
        if variant_uuid is not None:
            variant_filter |= Q(variant_id=variant_uuid)
        variant = variant_queryset.filter(variant_filter).first()
        if variant is None:
            return Response(
                {'error': 'Catalog variant not found in local projection'},
                status=status.HTTP_404_NOT_FOUND
            )

        defaults = {
            'name_snapshot': data.get('name') or variant.display_name or inventory.name,
            'sku_snapshot': variant.variant_sku or inventory.external_system_id or '',
            'barcode_snapshot': variant.variant_barcode or '',
            'description': inventory.description or '',
            'inventory_category': inventory.category,
            'inventory_type': inventory.inventory_type,
            'default_uom_code': inventory.unit or '',
            'stock_uom_code': inventory.unit_name or '',
            'track_stock': True,
            'track_lot': inventory.batch_tracking_enabled,
            'track_serial': inventory.trackable,
            'track_expiry': bool(inventory.expiration_threshold),
            'allow_negative_stock': False,
            'reorder_point': inventory.re_order_point,
            'reorder_quantity': inventory.re_order_quantity,
            'minimum_stock_level': inventory.minimum_stock_level,
            'safety_stock_level': inventory.safety_stock_level,
            'default_supplier': inventory.default_supplier,
            'product_template_id': variant.product_id,
            'metadata': {
                'legacy_inventory_id': str(inventory.id),
                'legacy_variant_barcode': variant.variant_barcode or '',
            },
            'created_by_user_id': get_request_user_id(request, as_str=False),
            'updated_by_user_id': get_request_user_id(request, as_str=False),
        }
        inventory_item, created = InventoryItem.objects.get_or_create(
            profile_id=profile_id,
            product_variant_id=variant.variant_id,
            defaults=defaults,
        )
        if not created:
            updated_fields = []
            for field_name, field_value in defaults.items():
                if field_name in {'metadata', 'created_by_user_id'}:
                    continue
                if getattr(inventory_item, field_name) != field_value:
                    setattr(inventory_item, field_name, field_value)
                    updated_fields.append(field_name)
            metadata = dict(inventory_item.metadata or {})
            metadata.update(defaults['metadata'])
            if metadata != (inventory_item.metadata or {}):
                inventory_item.metadata = metadata
                updated_fields.append('metadata')
            if updated_fields:
                inventory_item.updated_by_user_id = get_request_user_id(request, as_str=False)
                updated_fields.append('updated_by_user_id')
                inventory_item.save(update_fields=list(dict.fromkeys(updated_fields)))

        serializer = self.get_serializer(inventory_item)
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
        stock_items = self.get_queryset().filter(
            Q(id=InventoryItem.legacy_bridge_id(inventory.id)) |
            Q(metadata__legacy_inventory_id=str(inventory.id))
        )
        serializer = StockItemListSerializer(
            stock_items,
            many=True,
            context={'request': request, 'inventory_item_summary_map': get_inventory_item_summary_map(stock_items)},
        )
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def tracking_history(self, request, pk=None):
        """Get complete tracking history for stock item"""
        stock_item = self.get_object()
        tracking = stock_item.stock_movements.select_related(
            'from_location',
            'to_location',
            'stock_lot',
            'stock_serial',
        ).order_by('-occurred_at')

        serializer = StockMovementListSerializer(tracking, many=True,context={'request': request})
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
        """Get low stock inventory item rows for dashboard view."""
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
    queryset = StockReservation.objects.select_related('inventory_item', 'stock_location', 'stock_lot', 'stock_serial')
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
            queryset = queryset.filter(
                Q(inventory_item_id=InventoryItem.legacy_bridge_id(inventory_id)) |
                Q(inventory_item__metadata__legacy_inventory_id=str(inventory_id))
            )
        inventory_item_id = self.request.query_params.get('inventory_item')
        if inventory_item_id:
            queryset = queryset.filter(inventory_item_id=inventory_item_id)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        profile_id = get_request_profile_id(request, required=True, as_str=False)
        inventory = None
        inventory_item = None
        if data.get('inventory_item_id'):
            inventory_item = InventoryItem.objects.filter(
                id=data['inventory_item_id'],
                profile_id=profile_id,
            ).first()
            if inventory_item is None:
                return Response({'error': 'Inventory item not found'}, status=status.HTTP_404_NOT_FOUND)
        else:
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

        stock_serial = None
        stock_serial_id = data.get('stock_serial_id')
        if stock_serial_id:
            stock_serial = StockSerial.objects.filter(profile_id=profile_id, id=stock_serial_id).first()
            if stock_serial is None:
                return Response({'error': 'Stock serial not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = StockDomainService.reserve_stock(
                inventory=inventory,
                inventory_item=inventory_item,
                stock_location=stock_location,
                quantity=data['quantity'],
                external_order_type=data['external_order_type'],
                external_order_id=data['external_order_id'],
                external_order_line_id=data.get('external_order_line_id', ''),
                stock_lot=stock_lot,
                stock_serial=stock_serial,
                serial_number=data.get('serial_number', ''),
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
    
