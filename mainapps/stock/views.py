from datetime import timedelta
from decimal import Decimal
import uuid

from django.db.models import Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from mainapps.inventory.models import InventoryCategory, InventoryItem
from mainapps.inventory.serializers import StockAnalyticsSerializer
from mainapps.inventory.views import BaseInventoryViewSet
from mainapps.projections.models import CatalogVariantProjection
from mainapps.stock.models import (
    StockBalance,
    StockLocation,
    StockLocationType,
    StockLot,
    StockMovement,
    StockReservation,
    StockSerial,
)
from mainapps.stock.serializers import (
    InventoryItemDetailSerializer,
    InventoryItemListSerializer,
    LowStockBalanceSerializer,
    StockBalanceDetailSerializer,
    StockLocationDetailSerializer,
    StockLocationListSerializer,
    StockLocationTypeSerializer,
    StockLotDetailSerializer,
    StockMovementListSerializer,
    StockReservationCreateSerializer,
    StockReservationMutationSerializer,
    StockReservationSerializer,
    StockSerialDetailSerializer,
)
from subapps.kafka.producers.inventory_admin import (
    publish_inventory_admin_event,
    serialize_inventory_item,
    serialize_stock_location,
    serialize_stock_reservation,
)
from subapps.permissions.constants import UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset, CachingMixin, PermissionRequiredMixin
from subapps.services.inventory_read_model import (
    get_inventory_item_summary_map,
    get_low_stock_rows,
    get_profile_stock_analytics,
)
from subapps.services.location_scope import (
    get_location_scope_ids,
    get_location_scope_ids_for_locations,
    resolve_structural_locations,
)
from subapps.services.stock_domain import StockDomainError, StockDomainService
from subapps.utils.request_context import get_request_profile_id, get_request_user_id, scope_queryset_by_identity


def _audit_actor_from_request(request) -> dict[str, str]:
    return {
        'user_id': str(get_request_user_id(request, required=True) or '').strip(),
    }


def _resolve_location_scope_ids(*, profile_id, location_id):
    return get_location_scope_ids(profile_id=profile_id, stock_location_id=location_id)


def _apply_location_scope_filter(queryset, *, field_name: str, profile_id, location_id):
    scoped_location_ids = _resolve_location_scope_ids(profile_id=profile_id, location_id=location_id)
    if not scoped_location_ids:
        return queryset.none()
    return queryset.filter(**{f"{field_name}__in": scoped_location_ids})


def _get_scope_location_param(request):
    return request.query_params.get('stock_location') or request.query_params.get('structural_location_id')


def _get_scope_location_params(request, *, singular_keys, plural_keys):
    raw_ids: list[str] = []
    for key in singular_keys:
        value = request.query_params.get(key)
        if value:
            raw_ids.append(value)
    for key in plural_keys:
        raw_ids.extend(request.query_params.getlist(key))
        csv_value = request.query_params.get(key)
        if csv_value:
            raw_ids.extend([part.strip() for part in csv_value.split(',') if part.strip()])
    return raw_ids


def _get_scope_mode(request):
    value = (request.query_params.get('scope') or '').strip().lower()
    if value == 'all':
        return 'all_locations'
    return value


def filter_inventory_items_for_location(queryset, location_id, *, profile_id=None):
    if profile_id is None:
        location = StockLocation.objects.filter(id=location_id).first()
        profile_id = getattr(location, "profile_id", None)
    if profile_id is None:
        return queryset.none()
    return _apply_location_scope_filter(
        queryset,
        field_name="stock_balances__stock_location_id",
        profile_id=profile_id,
        location_id=location_id,
    ).distinct()


def filter_inventory_items_for_purchase_order(queryset, purchase_order_id):
    return queryset.filter(purchase_order_lines__purchase_order_id=purchase_order_id).distinct()


def filter_inventory_items_for_sales_order(queryset, sales_order_id):
    return queryset.filter(sales_order_lines__sales_order_id=sales_order_id).distinct()


class ReadStockLocationType(viewsets.ReadOnlyModelViewSet):
    serializer_class = StockLocationTypeSerializer
    queryset = StockLocationType.objects.all()


class StockLocationViewSet(BaseInventoryViewSet):
    required_permission = UNIFIED_PERMISSION_DICT.get('stock_location')
    queryset = StockLocation.objects.select_related('location_type', 'parent')
    filterset_fields = ['structural', 'external', 'location_type', 'parent']
    search_fields = ['name', 'code', 'description']
    ordering_fields = ['name', 'code', 'created_at']
    ordering = ['name']

    def perform_create(self, serializer):
        from subapps.services.subscription_entitlements import enforce_subscription_limit
        profile_id = get_request_profile_id(self.request, required=True, as_str=False)
        if serializer.validated_data.get('structural', False):
            usage = self.get_queryset().filter(structural=True).count()
            enforce_subscription_limit(profile_id=profile_id, feature='structural-locations', usage=usage)
        super().perform_create(serializer)
        payload = serialize_stock_location(serializer.instance)
        publish_inventory_admin_event(
            event_name='inventory.stock_location.created',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'stock_location',
                'id': payload['stock_location_id'],
                'label': payload['name'],
            },
            summary=f"Stock location created: {payload['name']}.",
            metadata={
                'code': payload['code'],
                'structural': payload['structural'],
                'external': payload['external'],
                'location_type_name': payload['location_type_name'],
            },
            after=payload,
            feature_area='stock_topology',
            reference_number=payload['code'],
        )

    def perform_update(self, serializer):
        before = serialize_stock_location(self.get_object())
        super().perform_update(serializer)
        payload = serialize_stock_location(serializer.instance)
        publish_inventory_admin_event(
            event_name='inventory.stock_location.updated',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'stock_location',
                'id': payload['stock_location_id'],
                'label': payload['name'],
            },
            summary=f"Stock location updated: {payload['name']}.",
            metadata={
                'code': payload['code'],
                'structural': payload['structural'],
                'external': payload['external'],
                'location_type_name': payload['location_type_name'],
            },
            before=before,
            after=payload,
            feature_area='stock_topology',
            reference_number=payload['code'],
        )

    def destroy(self, request, *args, **kwargs):
        location = self.get_object()
        before = serialize_stock_location(location)
        response = super().destroy(request, *args, **kwargs)
        publish_inventory_admin_event(
            event_name='inventory.stock_location.deleted',
            payload=before,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'stock_location',
                'id': before['stock_location_id'],
                'label': before['name'],
            },
            summary=f"Stock location deleted: {before['name']}.",
            metadata={
                'code': before['code'],
                'structural': before['structural'],
                'external': before['external'],
                'location_type_name': before['location_type_name'],
            },
            before=before,
            after={},
            severity='warning',
            feature_area='stock_topology',
            reference_number=before['code'],
        )
        return response

    def get_serializer_class(self):
        if self.action == 'list':
            return StockLocationListSerializer
        return StockLocationDetailSerializer

    @action(detail=True, methods=['get'])
    def inventory_items(self, request, pk=None):
        location = self.get_object()
        inventory_item_ids = set(
            StockBalance.objects.filter(
                profile_id=location.profile_id,
                stock_location_id__in=_resolve_location_scope_ids(profile_id=location.profile_id, location_id=location.id) or [location.id],
            ).values_list('inventory_item_id', flat=True)
        )
        inventory_items = InventoryItem.objects.filter(id__in=inventory_item_ids).order_by('-created_at')
        summary_map = get_inventory_item_summary_map(inventory_items, stock_location=location)

        status_filter = request.query_params.get('status')
        if status_filter:
            matching_ids = [
                item.id for item in inventory_items
                if summary_map.get(item.id, {}).get('status') == status_filter or item.status == status_filter
            ]
            inventory_items = inventory_items.filter(id__in=matching_ids)
            summary_map = {item_id: summary for item_id, summary in summary_map.items() if item_id in matching_ids}

        serializer = InventoryItemListSerializer(
            inventory_items,
            many=True,
            context={'request': request, 'inventory_item_summary_map': summary_map},
        )
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def transfer_stock(self, request, pk=None):
        from_location = self.get_object()
        data = request.data
        to_location_id = data.get('to_location_id')
        structural_scope_location_id = data.get('structural_location_id') or request.query_params.get('structural_location_id')
        inventory_item_id = data.get('inventory_item_id')
        stock_lot_id = data.get('stock_lot_id')
        stock_serial_id = data.get('stock_serial_id')
        serial_number = data.get('serial_number', '')

        try:
            quantity = Decimal(str(data.get('quantity', 0)))
        except Exception:
            return Response({'error': 'quantity must be a valid number'}, status=status.HTTP_400_BAD_REQUEST)

        if not to_location_id or not inventory_item_id or quantity <= 0:
            return Response(
                {'error': 'to_location_id, inventory_item_id, and quantity are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile_id = get_request_profile_id(request, required=True, as_str=False)
        inventory_item = scope_queryset_by_identity(
            InventoryItem.objects.filter(id=inventory_item_id),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        ).first()
        if inventory_item is None:
            return Response({'error': 'Inventory item not found'}, status=status.HTTP_404_NOT_FOUND)

        to_location = scope_queryset_by_identity(
            StockLocation.objects.filter(id=to_location_id),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        ).first()
        if to_location is None:
            return Response({'error': 'Destination location not found'}, status=status.HTTP_404_NOT_FOUND)
        if structural_scope_location_id:
            scoped_location_ids = _resolve_location_scope_ids(profile_id=profile_id, location_id=structural_scope_location_id)
            if not scoped_location_ids:
                return Response({'error': 'The selected structural location scope is unavailable'}, status=status.HTTP_400_BAD_REQUEST)
            if from_location.id not in scoped_location_ids or to_location.id not in scoped_location_ids:
                return Response(
                    {'error': 'Source and destination locations must belong to the selected structural location scope'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        stock_lot = None
        if stock_lot_id:
            stock_lot = StockLot.objects.filter(id=stock_lot_id, profile_id=profile_id, inventory_item=inventory_item).first()
            if stock_lot is None:
                return Response({'error': 'Stock lot not found for the selected inventory item'}, status=status.HTTP_404_NOT_FOUND)

        stock_serial = None
        if stock_serial_id:
            stock_serial = StockSerial.objects.filter(id=stock_serial_id, profile_id=profile_id, inventory_item=inventory_item).first()
            if stock_serial is None:
                return Response({'error': 'Stock serial not found for the selected inventory item'}, status=status.HTTP_404_NOT_FOUND)

        try:
            transfer_result = StockDomainService.transfer_stock(
                inventory_item=inventory_item,
                from_location=from_location,
                to_location=to_location,
                quantity=quantity,
                actor_user_id=get_request_user_id(request, as_str=False),
                stock_lot=stock_lot,
                stock_serial=stock_serial,
                serial_number=serial_number,
            )
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        item_payload = serialize_inventory_item(inventory_item)
        publish_inventory_admin_event(
            event_name='inventory.stock.transferred',
            payload={
                **item_payload,
                'from_location_id': str(from_location.id),
                'from_location_name': from_location.name,
                'to_location_id': str(to_location.id),
                'to_location_name': to_location.name,
                'quantity': float(quantity),
                'source_quantity_after': float(transfer_result['source_balance'].quantity_on_hand),
                'destination_quantity_after': float(transfer_result['destination_balance'].quantity_on_hand),
                'stock_lot_id': str(stock_lot.id) if stock_lot else '',
                'stock_serial_id': str(stock_serial.id) if stock_serial else '',
            },
            actor=_audit_actor_from_request(request),
            target={
                'type': 'inventory_item',
                'id': item_payload['inventory_item_id'],
                'label': item_payload['name_snapshot'],
                'barcode': item_payload['barcode_snapshot'],
                'sku': item_payload['sku_snapshot'],
            },
            summary=f"Stock transferred for {item_payload['name_snapshot']} from {from_location.name} to {to_location.name}.",
            metadata={
                'from_location_name': from_location.name,
                'to_location_name': to_location.name,
                'quantity': float(quantity),
            },
            after={
                'from_location_id': str(from_location.id),
                'to_location_id': str(to_location.id),
                'quantity': float(quantity),
            },
            feature_area='stock_control',
            reference_number=item_payload['sku_snapshot'] or item_payload['barcode_snapshot'],
            notification_category='stock_alert',
            notification_title=f"Stock transferred for {item_payload['name_snapshot']}",
            notification_message=(
                f"{float(quantity)} unit{'s' if quantity != 1 else ''} of {item_payload['name_snapshot']} "
                f"were transferred from {from_location.name} to {to_location.name}."
            ),
            notification_action_url='/inventory',
        )

        return Response({
            'message': 'Stock transferred successfully',
            'transferred_quantity': quantity,
            'from_location': from_location.name,
            'to_location': to_location.name,
        })


class BaseInventoryViewSetMixin(CachingMixin, PermissionRequiredMixin, viewsets.ModelViewSet):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    profile_scope_field = 'profile_id'
    legacy_profile_scope_field = 'profile'

    def get_queryset(self):
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


class BaseInventoryReadOnlyViewSetMixin(CachingMixin, PermissionRequiredMixin, viewsets.ReadOnlyModelViewSet):
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    profile_scope_field = 'profile_id'
    legacy_profile_scope_field = 'profile'

    def get_queryset(self):
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


class InventoryItemViewSet(BaseInventoryViewSetMixin):
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_item')
    queryset = InventoryItem.objects.select_related('inventory_category', 'default_supplier')
    filterset_fields = ['status', 'inventory_category', 'inventory_type', 'default_supplier']
    search_fields = ['name_snapshot', 'sku_snapshot', 'barcode_snapshot']
    ordering_fields = ['name_snapshot', 'created_at', 'minimum_stock_level', 'reorder_point']
    ordering = ['-created_at']
    serializer_class = InventoryItemDetailSerializer

    def get_serializer_class(self):
        if self.action == 'list':
            return InventoryItemListSerializer
        return InventoryItemDetailSerializer

    def perform_create(self, serializer):
        profile_id = get_request_profile_id(self.request, required=True, as_str=False)
        user_id = get_request_user_id(self.request, required=True, as_str=False)
        inventory_item = serializer.save(
            profile_id=profile_id,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        payload = serialize_inventory_item(inventory_item)
        publish_inventory_admin_event(
            event_name='inventory.item.created',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'inventory_item',
                'id': payload['inventory_item_id'],
                'label': payload['name_snapshot'],
                'barcode': payload['barcode_snapshot'],
                'sku': payload['sku_snapshot'],
            },
            summary=f"Inventory item created: {payload['name_snapshot']}.",
            metadata={
                'inventory_type': payload['inventory_type'],
                'status': payload['status'],
                'category_name': payload['inventory_category_name'],
            },
            after=payload,
            feature_area='inventory_master',
            reference_number=payload['sku_snapshot'] or payload['barcode_snapshot'],
            notification_category='stock_alert',
            notification_title=f"Inventory item {payload['name_snapshot']} created",
            notification_message=f"Inventory item {payload['name_snapshot']} was added to the workspace catalog.",
            notification_action_url='/inventory',
        )

    def perform_update(self, serializer):
        before = serialize_inventory_item(self.get_object())
        inventory_item = serializer.save(updated_by_user_id=get_request_user_id(self.request, required=True, as_str=False))
        payload = serialize_inventory_item(inventory_item)
        publish_inventory_admin_event(
            event_name='inventory.item.updated',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'inventory_item',
                'id': payload['inventory_item_id'],
                'label': payload['name_snapshot'],
                'barcode': payload['barcode_snapshot'],
                'sku': payload['sku_snapshot'],
            },
            summary=f"Inventory item updated: {payload['name_snapshot']}.",
            metadata={
                'inventory_type': payload['inventory_type'],
                'status': payload['status'],
                'category_name': payload['inventory_category_name'],
            },
            before=before,
            after=payload,
            feature_area='inventory_master',
            reference_number=payload['sku_snapshot'] or payload['barcode_snapshot'],
        )

    def destroy(self, request, *args, **kwargs):
        inventory_item = self.get_object()
        before = serialize_inventory_item(inventory_item)
        response = super().destroy(request, *args, **kwargs)
        publish_inventory_admin_event(
            event_name='inventory.item.deleted',
            payload=before,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'inventory_item',
                'id': before['inventory_item_id'],
                'label': before['name_snapshot'],
                'barcode': before['barcode_snapshot'],
                'sku': before['sku_snapshot'],
            },
            summary=f"Inventory item deleted: {before['name_snapshot']}.",
            metadata={
                'inventory_type': before['inventory_type'],
                'status': before['status'],
                'category_name': before['inventory_category_name'],
            },
            before=before,
            after={},
            severity='warning',
            feature_area='inventory_master',
            reference_number=before['sku_snapshot'] or before['barcode_snapshot'],
        )
        return response

    def _get_requested_stock_location(self):
        profile_id = get_request_profile_id(self.request, as_str=False)
        if not profile_id:
            return None
        location_id = (
            self.request.query_params.get('structural_location_id')
            or self.request.query_params.get('stock_location')
            or self.request.query_params.get('location')
        )
        if not location_id:
            return None
        return scope_queryset_by_identity(
            StockLocation.objects.filter(id=location_id),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        ).first()

    def _get_requested_structural_locations(self):
        profile_id = get_request_profile_id(self.request, as_str=False)
        if not profile_id:
            return []
        raw_ids = _get_scope_location_params(
            self.request,
            singular_keys=('structural_location_id', 'stock_location', 'location'),
            plural_keys=('structural_location_ids', 'stock_location_ids'),
        )
        return resolve_structural_locations(profile_id=profile_id, stock_location_ids=raw_ids)

    def _get_scope_mode(self):
        return _get_scope_mode(self.request)

    def _get_inventory_scope(self):
        if self._get_scope_mode() == 'all_locations':
            return None, []
        structural_locations = self._get_requested_structural_locations()
        return (structural_locations[0], structural_locations) if structural_locations else (None, [])

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        stock_location, stock_locations = self._get_inventory_scope()
        if self.action in {'list', 'expiring_soon'}:
            try:
                context['inventory_item_summary_map'] = get_inventory_item_summary_map(
                    list(self.get_queryset()),
                    stock_location=stock_location,
                    stock_locations=stock_locations,
                )
            except Exception:
                context['inventory_item_summary_map'] = {}
        elif self.action == 'retrieve':
            try:
                target_id = self.kwargs.get(self.lookup_field or 'pk')
                target_item = self.get_queryset().filter(pk=target_id).first() if target_id else None
                context['inventory_item_summary_map'] = (
                    get_inventory_item_summary_map(
                        [target_item],
                        stock_location=stock_location,
                        stock_locations=stock_locations,
                    ) if target_item is not None else {}
                )
            except Exception:
                context['inventory_item_summary_map'] = {}
        return context

    def get_queryset(self):
        queryset = super().get_queryset()
        location = self.request.query_params.get('location')
        _, stock_locations = self._get_inventory_scope()
        purchase_order = self.request.query_params.get('purchase_order')
        sales_order = self.request.query_params.get('sales_order')
        product_variant = self.request.query_params.get('product_variant')
        inventory_item_id = self.request.query_params.get('inventory_item')
        category_id = self.request.query_params.get('inventory_category')

        if inventory_item_id:
            queryset = queryset.filter(id=inventory_item_id)
        if category_id:
            queryset = queryset.filter(inventory_category_id=category_id)
        if location:
            queryset = filter_inventory_items_for_location(queryset, location, profile_id=get_request_profile_id(self.request, as_str=False))
        elif stock_locations:
            scoped_location_ids = get_location_scope_ids_for_locations(
                profile_id=get_request_profile_id(self.request, required=True, as_str=False),
                stock_locations=stock_locations,
            )
            queryset = (
                queryset.filter(stock_balances__stock_location_id__in=scoped_location_ids).distinct()
                if scoped_location_ids
                else queryset.none()
            )
        if purchase_order:
            queryset = filter_inventory_items_for_purchase_order(queryset, purchase_order)
        if sales_order:
            queryset = filter_inventory_items_for_sales_order(queryset, sales_order)
        if product_variant:
            queryset = queryset.filter(Q(barcode_snapshot=product_variant) | Q(product_variant_id=product_variant))

        expiry_filter = self.request.query_params.get('expiry_status')
        if expiry_filter == 'expired':
            queryset = queryset.filter(stock_lots__expiry_date__lt=timezone.now().date()).distinct()
        elif expiry_filter == 'expiring_soon':
            queryset = queryset.filter(
                stock_lots__expiry_date__lte=timezone.now().date() + timedelta(days=30),
                stock_lots__expiry_date__gt=timezone.now().date(),
            ).distinct()

        quantity_filter = self.request.query_params.get('quantity_filter')
        if quantity_filter in {'zero', 'low'}:
            stock_location, stock_locations = self._get_inventory_scope()
            summary_map = get_inventory_item_summary_map(
                list(queryset),
                stock_location=stock_location,
                stock_locations=stock_locations,
            )
            matching_ids = []
            for item in queryset:
                quantity = summary_map.get(item.id, {}).get('quantity', Decimal('0'))
                if quantity_filter == 'zero' and quantity <= 0:
                    matching_ids.append(item.id)
                minimum_stock_level = Decimal(str(item.minimum_stock_level or 0))
                if quantity_filter == 'low' and minimum_stock_level > 0 and Decimal('0') < quantity <= minimum_stock_level:
                    matching_ids.append(item.id)
            queryset = queryset.filter(id__in=matching_ids) if matching_ids else queryset.none()

        return queryset
    @action(detail=False, methods=['get'])
    def expiring_soon(self, request):
        days = int(request.query_params.get('days', 30))
        cutoff_date = timezone.now().date() + timedelta(days=days)
        queryset = self.get_queryset().filter(
            stock_lots__expiry_date__lte=cutoff_date,
            stock_lots__expiry_date__gt=timezone.now().date(),
        ).distinct().order_by('stock_lots__expiry_date')
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        inventory_item = self.get_object()
        before = serialize_inventory_item(inventory_item)
        new_status = request.data.get('status')
        reason = request.data.get('reason', '')
        if not new_status:
            return Response({'error': 'status is required'}, status=status.HTTP_400_BAD_REQUEST)

        old_status = inventory_item.status
        inventory_item.status = new_status
        inventory_item.updated_by_user_id = get_request_user_id(request, as_str=False)
        inventory_item.save(update_fields=['status', 'updated_by_user_id', 'updated_at'])

        StockMovement.objects.create(
            profile_id=inventory_item.profile_id,
            inventory_item=inventory_item,
            movement_type='adjustment',
            quantity=Decimal('0'),
            reference_type='inventory_item_status',
            reference_id=str(inventory_item.id),
            actor_user_id=get_request_user_id(request, as_str=False),
            notes=f"Status changed from {old_status} to {new_status}. Reason: {reason}",
            created_by_user_id=get_request_user_id(request, as_str=False),
            updated_by_user_id=get_request_user_id(request, as_str=False),
        )

        after = serialize_inventory_item(inventory_item)
        publish_inventory_admin_event(
            event_name='inventory.item.status.updated',
            payload=after,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'inventory_item',
                'id': after['inventory_item_id'],
                'label': after['name_snapshot'],
                'barcode': after['barcode_snapshot'],
                'sku': after['sku_snapshot'],
            },
            summary=f"Inventory item status updated for {after['name_snapshot']}.",
            metadata={
                'reason': reason,
                'old_status': old_status,
                'new_status': new_status,
            },
            before=before,
            after=after,
            severity='warning' if new_status in {'archived', 'discontinued'} else 'info',
            feature_area='inventory_master',
            reference_number=after['sku_snapshot'] or after['barcode_snapshot'],
            notification_category='stock_alert',
            notification_title=f"Inventory item {after['name_snapshot']} status changed",
            notification_message=(
                f"Inventory item {after['name_snapshot']} moved from {old_status} to {new_status}."
            ),
            notification_action_url='/inventory',
        )

        return Response({'message': 'Status updated successfully', 'old_status': old_status, 'new_status': new_status})

    @action(detail=False, methods=['post'])
    def create_for_variants(self, request):
        data = request.data
        product_variant = data.get('product_variant')
        if not product_variant:
            return Response({'error': 'product_variant is required'}, status=status.HTTP_400_BAD_REQUEST)

        profile_id = get_request_profile_id(request, required=True, as_str=False)
        variant_queryset = CatalogVariantProjection.objects.select_related('product').filter(profile_id=profile_id)
        try:
            variant_uuid = uuid.UUID(str(product_variant))
        except (TypeError, ValueError, AttributeError):
            variant_uuid = None
        variant_filter = Q(variant_barcode=product_variant)
        if variant_uuid is not None:
            variant_filter |= Q(variant_id=variant_uuid)
        variant = variant_queryset.filter(variant_filter).first()
        if variant is None:
            return Response({'error': 'Catalog variant not found in local projection'}, status=status.HTTP_404_NOT_FOUND)

        inventory_category = None
        if data.get('inventory_category_id'):
            inventory_category = scope_queryset_by_identity(
                InventoryCategory.objects.filter(id=data.get('inventory_category_id')),
                canonical_field='profile_id',
                legacy_field='profile',
                value=profile_id,
            ).first()
            if inventory_category is None:
                return Response({'error': 'Inventory category not found'}, status=status.HTTP_404_NOT_FOUND)

        defaults = {
            'name_snapshot': data.get('name') or variant.display_name,
            'sku_snapshot': variant.variant_sku or '',
            'barcode_snapshot': variant.variant_barcode or '',
            'product_variant_image_url': variant.image_url or '',
            'description': data.get('description') or '',
            'inventory_category': inventory_category,
            'inventory_type': data.get('inventory_type') or 'finished_good',
            'default_uom_code': data.get('default_uom_code') or '',
            'stock_uom_code': data.get('stock_uom_code') or '',
            'track_stock': data.get('track_stock', True),
            'track_lot': data.get('track_lot', False),
            'track_serial': data.get('track_serial', False),
            'track_expiry': data.get('track_expiry', False),
            'allow_negative_stock': data.get('allow_negative_stock', False),
            'reorder_point': data.get('reorder_point', 0),
            'reorder_quantity': data.get('reorder_quantity', 0),
            'minimum_stock_level': data.get('minimum_stock_level', 0),
            'safety_stock_level': data.get('safety_stock_level', 0),
            'product_template_id': variant.product_id,
            'metadata': {'catalog_variant_barcode': variant.variant_barcode or ''},
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

        payload = serialize_inventory_item(inventory_item)
        publish_inventory_admin_event(
            event_name='inventory.item.created_from_variant' if created else 'inventory.item.synced_from_variant',
            payload=payload,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'inventory_item',
                'id': payload['inventory_item_id'],
                'label': payload['name_snapshot'],
                'barcode': payload['barcode_snapshot'],
                'sku': payload['sku_snapshot'],
            },
            summary=(
                f"Inventory item {'created' if created else 'synchronized'} from catalog variant for {payload['name_snapshot']}."
            ),
            metadata={
                'product_variant_id': payload['product_variant_id'],
                'product_template_id': payload['product_template_id'],
            },
            after=payload,
            feature_area='inventory_master',
            reference_number=payload['sku_snapshot'] or payload['barcode_snapshot'],
        )

        serializer = self.get_serializer(inventory_item)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def tracking_history(self, request, pk=None):
        inventory_item = self.get_object()
        tracking = inventory_item.stock_movements.select_related('from_location', 'to_location', 'stock_lot', 'stock_serial').order_by('-occurred_at')
        serializer = StockMovementListSerializer(tracking, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def analytics(self, request):
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        stock_location, stock_locations = self._get_inventory_scope()
        serializer = StockAnalyticsSerializer(
            get_profile_stock_analytics(
                profile_id=profile_id,
                stock_location=stock_location,
                stock_locations=stock_locations,
            ),
            context={'request': request},
        )
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        stock_location, stock_locations = self._get_inventory_scope()
        rows = get_low_stock_rows(
            self.get_queryset(),
            stock_location=stock_location,
            stock_locations=stock_locations,
        )
        page = self.paginate_queryset(rows)
        if page is not None:
            serializer = LowStockBalanceSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)
        serializer = LowStockBalanceSerializer(rows, many=True, context={'request': request})
        return Response(serializer.data)


class StockBalanceViewSet(BaseInventoryReadOnlyViewSetMixin):
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_item')
    queryset = StockBalance.objects.select_related('inventory_item', 'stock_location', 'stock_lot')
    serializer_class = StockBalanceDetailSerializer
    filterset_fields = ['inventory_item', 'stock_location', 'stock_lot']
    search_fields = ['inventory_item__name_snapshot', 'stock_location__name', 'stock_lot__lot_number']
    ordering_fields = ['quantity_on_hand', 'quantity_reserved', 'quantity_available', 'created_at', 'stock_location__name']
    ordering = ['stock_location__name', 'inventory_item__name_snapshot']

    def get_queryset(self):
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        stock_location_id = _get_scope_location_param(self.request)
        if profile_id and stock_location_id:
            queryset = _apply_location_scope_filter(
                queryset,
                field_name='stock_location_id',
                profile_id=profile_id,
                location_id=stock_location_id,
            )
        return queryset


class StockLotViewSet(BaseInventoryReadOnlyViewSetMixin):
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_item')
    queryset = StockLot.objects.select_related('inventory_item', 'supplier')
    serializer_class = StockLotDetailSerializer
    filterset_fields = ['inventory_item', 'supplier', 'status']
    search_fields = ['lot_number', 'inventory_item__name_snapshot', 'supplier__name']
    ordering_fields = ['expiry_date', 'remaining_quantity', 'received_quantity', 'created_at']
    ordering = ['expiry_date', '-created_at']


class StockSerialViewSet(BaseInventoryReadOnlyViewSetMixin):
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_item')
    queryset = StockSerial.objects.select_related('inventory_item', 'stock_location', 'stock_lot')
    serializer_class = StockSerialDetailSerializer
    filterset_fields = ['inventory_item', 'stock_location', 'stock_lot', 'status']
    search_fields = ['serial_number', 'inventory_item__name_snapshot', 'stock_location__name', 'stock_lot__lot_number']
    ordering_fields = ['serial_number', 'created_at', 'stock_location__name']
    ordering = ['serial_number']

    def get_queryset(self):
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        stock_location_id = _get_scope_location_param(self.request)
        if profile_id and stock_location_id:
            queryset = _apply_location_scope_filter(
                queryset,
                field_name='stock_location_id',
                profile_id=profile_id,
                location_id=stock_location_id,
            )
        return queryset


class StockMovementViewSet(BaseInventoryReadOnlyViewSetMixin):
    required_permission = UNIFIED_PERMISSION_DICT.get('inventory_item')
    queryset = StockMovement.objects.select_related('inventory_item', 'from_location', 'to_location', 'stock_lot', 'stock_serial')
    serializer_class = StockMovementListSerializer
    filterset_fields = ['inventory_item', 'movement_type', 'reference_type', 'reference_id', 'from_location', 'to_location', 'stock_lot', 'stock_serial']
    search_fields = [
        'inventory_item__name_snapshot',
        'reference_type',
        'reference_id',
        'from_location__name',
        'to_location__name',
        'stock_lot__lot_number',
        'stock_serial__serial_number',
        'notes',
    ]
    ordering_fields = ['occurred_at', 'created_at', 'quantity', 'movement_type']
    ordering = ['-occurred_at', '-created_at']

    def get_queryset(self):
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        stock_location_id = _get_scope_location_param(self.request)
        if profile_id and stock_location_id:
            scoped_location_ids = _resolve_location_scope_ids(profile_id=profile_id, location_id=stock_location_id)
            if not scoped_location_ids:
                return queryset.none()
            queryset = queryset.filter(
                Q(from_location_id__in=scoped_location_ids) | Q(to_location_id__in=scoped_location_ids)
            )
        return queryset


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

        inventory_item_id = self.request.query_params.get('inventory_item')
        if inventory_item_id:
            queryset = queryset.filter(inventory_item_id=inventory_item_id)
        stock_location_id = _get_scope_location_param(self.request)
        if profile_id and stock_location_id:
            queryset = _apply_location_scope_filter(
                queryset,
                field_name='stock_location_id',
                profile_id=profile_id,
                location_id=stock_location_id,
            )
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        profile_id = get_request_profile_id(request, required=True, as_str=False)

        inventory_item = InventoryItem.objects.filter(id=data['inventory_item_id'], profile_id=profile_id).first()
        if inventory_item is None:
            return Response({'error': 'Inventory item not found'}, status=status.HTTP_404_NOT_FOUND)

        stock_location = scope_queryset_by_identity(
            StockLocation.objects.filter(id=data['location_id']),
            canonical_field='profile_id',
            legacy_field='profile',
            value=profile_id,
        ).first()
        if stock_location is None:
            return Response({'error': 'Stock location not found'}, status=status.HTTP_404_NOT_FOUND)
        structural_scope_location_id = data.get('structural_location_id') or request.query_params.get('structural_location_id')
        if structural_scope_location_id:
            scoped_location_ids = _resolve_location_scope_ids(profile_id=profile_id, location_id=structural_scope_location_id)
            if not scoped_location_ids:
                return Response({'error': 'The selected structural location scope is unavailable'}, status=status.HTTP_400_BAD_REQUEST)
            if stock_location.id not in scoped_location_ids:
                return Response({'error': 'Stock location is outside the selected structural location scope'}, status=status.HTTP_400_BAD_REQUEST)

        stock_lot = None
        if data.get('stock_lot_id'):
            stock_lot = StockLot.objects.filter(profile_id=profile_id, id=data['stock_lot_id']).first()
            if stock_lot is None:
                return Response({'error': 'Stock lot not found'}, status=status.HTTP_404_NOT_FOUND)

        stock_serial = None
        if data.get('stock_serial_id'):
            stock_serial = StockSerial.objects.filter(profile_id=profile_id, id=data['stock_serial_id']).first()
            if stock_serial is None:
                return Response({'error': 'Stock serial not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = StockDomainService.reserve_stock(
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

        reservation = result['reservation']
        reservation_payload = serialize_stock_reservation(reservation)
        publish_inventory_admin_event(
            event_name='inventory.stock_reservation.created',
            payload=reservation_payload,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'stock_reservation',
                'id': reservation_payload['stock_reservation_id'],
                'label': f"{reservation_payload['inventory_name']} reservation",
                'barcode': reservation_payload['inventory_barcode'],
                'sku': reservation_payload['inventory_sku'],
            },
            summary=(
                f"Reserved {reservation_payload['reserved_quantity']} of {reservation_payload['inventory_name']} "
                f"at {reservation_payload['stock_location_name']}."
            ),
            metadata={
                'external_order_type': reservation_payload['external_order_type'],
                'external_order_id': reservation_payload['external_order_id'],
                'stock_location_name': reservation_payload['stock_location_name'],
                'lot_number': reservation_payload['lot_number'],
                'serial_number': reservation_payload['serial_number'],
            },
            after=reservation_payload,
            feature_area='stock_control',
            reference_number=reservation_payload['inventory_sku'] or reservation_payload['inventory_barcode'],
            notification_category='stock_alert',
            notification_title=f"Stock reserved for {reservation_payload['inventory_name']}",
            notification_message=(
                f"{reservation_payload['reserved_quantity']} unit(s) of {reservation_payload['inventory_name']} "
                f"were reserved at {reservation_payload['stock_location_name']}."
            ),
            notification_action_url='/inventory',
        )

        output = StockReservationSerializer(reservation, context=self.get_serializer_context())
        return Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        reservation = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = serialize_stock_reservation(reservation)
        try:
            result = StockDomainService.release_reservation(
                reservation=reservation,
                quantity=serializer.validated_data.get('quantity'),
                actor_user_id=get_request_user_id(request, as_str=False),
                notes=serializer.validated_data.get('notes', ''),
            )
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        released_reservation = result['reservation']
        after = serialize_stock_reservation(released_reservation)
        release_quantity = serializer.validated_data.get('quantity')
        publish_inventory_admin_event(
            event_name='inventory.stock_reservation.released',
            payload=after,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'stock_reservation',
                'id': after['stock_reservation_id'],
                'label': f"{after['inventory_name']} reservation",
                'barcode': after['inventory_barcode'],
                'sku': after['inventory_sku'],
            },
            summary=(
                f"Released {release_quantity or after['remaining_quantity']} of {after['inventory_name']} "
                f"from reservation at {after['stock_location_name']}."
            ),
            metadata={
                'external_order_type': after['external_order_type'],
                'external_order_id': after['external_order_id'],
                'released_quantity': str(release_quantity or ''),
                'stock_location_name': after['stock_location_name'],
            },
            before=before,
            after=after,
            feature_area='stock_control',
            reference_number=after['inventory_sku'] or after['inventory_barcode'],
            notification_category='stock_alert',
            notification_title=f"Reservation released for {after['inventory_name']}",
            notification_message=(
                f"Reserved stock for {after['inventory_name']} was released at {after['stock_location_name']}."
            ),
            notification_action_url='/inventory',
        )
        output = StockReservationSerializer(released_reservation, context=self.get_serializer_context())
        return Response(output.data)

    @action(detail=True, methods=['post'])
    def fulfill(self, request, pk=None):
        reservation = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = serialize_stock_reservation(reservation)
        try:
            result = StockDomainService.fulfill_reservation(
                reservation=reservation,
                quantity=serializer.validated_data.get('quantity'),
                actor_user_id=get_request_user_id(request, as_str=False),
                notes=serializer.validated_data.get('notes', ''),
            )
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        fulfilled_reservation = result['reservation']
        after = serialize_stock_reservation(fulfilled_reservation)
        fulfilled_quantity = serializer.validated_data.get('quantity')
        publish_inventory_admin_event(
            event_name='inventory.stock_reservation.fulfilled',
            payload=after,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'stock_reservation',
                'id': after['stock_reservation_id'],
                'label': f"{after['inventory_name']} reservation",
                'barcode': after['inventory_barcode'],
                'sku': after['inventory_sku'],
            },
            summary=(
                f"Fulfilled {fulfilled_quantity or after['fulfilled_quantity']} of {after['inventory_name']} "
                f"from reservation at {after['stock_location_name']}."
            ),
            metadata={
                'external_order_type': after['external_order_type'],
                'external_order_id': after['external_order_id'],
                'fulfilled_quantity': str(fulfilled_quantity or ''),
                'stock_location_name': after['stock_location_name'],
            },
            before=before,
            after=after,
            feature_area='stock_control',
            reference_number=after['inventory_sku'] or after['inventory_barcode'],
            notification_category='stock_alert',
            notification_title=f"Reservation fulfilled for {after['inventory_name']}",
            notification_message=(
                f"Reserved stock for {after['inventory_name']} was fulfilled from {after['stock_location_name']}."
            ),
            notification_action_url='/inventory',
        )
        output = StockReservationSerializer(fulfilled_reservation, context=self.get_serializer_context())
        return Response(output.data)
