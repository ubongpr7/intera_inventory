from django.conf import settings
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Prefetch, Q, Sum, Count, Avg, F, Case, When, Value, IntegerField, DecimalField
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
import logging

from mainapps.orders.serializers import (
    GoodsReceiptDetailSerializer,
    GoodsReceiptListSerializer,
    PurchaseOrderDetailSerializer,
    PurchaseOrderAnalyticsSerializer,
    PurchaseOrderLineItemSerializer,
    PurchaseOrderLineItemCreateSerializer,
    PurchaseOrderListSerializer,
    ReceiveItemsSerializer,
    ReturnOrderCreateSerializer,
    ReturnOrderDetailSerializer,
    ReturnOrderListSerializer,
    ReturnOrderProcessSerializer,
    SalesOrderDetailSerializer,
    SalesOrderLineItemCreateSerializer,
    SalesOrderLineItemSerializer,
    SalesOrderListSerializer,
    SalesOrderReleaseSerializer,
    SalesOrderReserveSerializer,
    SalesOrderShipSerializer,
    SalesOrderShipmentDetailSerializer,
    SalesOrderShipmentListSerializer,
    SalesOrderShipmentSerializer,
)
from mainapps.stock.models import (
    StockLocation,
    StockLot,
    StockMovementType,
    TrackingType,
    StockReservation,
    StockReservationStatus,
    StockSerial,
)
from mainapps.identity.models import IdentityCompanyProfile
from subapps.permissions.constants import PURCHASE_ORDER_PERMISSIONS, UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset, HasModelRequestPermission, PermissionRequiredMixin
from subapps.kafka.producers import (
    publish_order_admin_event,
    serialize_goods_receipt,
    serialize_goods_receipt_line,
    serialize_purchase_order,
    serialize_purchase_order_line_item,
    serialize_return_order,
    serialize_return_order_line_item,
    serialize_sales_order,
    serialize_sales_order_line_item,
    serialize_sales_order_shipment,
)
from subapps.kafka.producers.platform_events import publish_notification_dispatch
from subapps.services.emails.email_services import EmailService, get_workspace_display_name
from subapps.services.location_scope import get_location_scope_ids, get_location_scope_ids_for_locations
from subapps.services.pdf.pdf_service import PDFService, PDFServiceUnavailableError
from subapps.services.pdf.notification_documents import (
    NotificationDocumentError,
    build_purchase_order_pdf_url,
    build_return_order_pdf_url,
    verify_purchase_order_pdf_token,
    verify_return_order_pdf_token,
)
from subapps.services.identity_directory import IdentityDirectory
from subapps.services.stock_domain import StockDomainError, StockDomainService
from subapps.utils.request_context import (
    get_request_profile_id,
    get_request_user_id,
    scope_queryset_by_identity,
)

from .pagination import PurchaseOrderPagination

from .models import (
    GoodsReceipt, GoodsReceiptLine,
    PurchaseOrder, PurchaseOrderLineItem, PurchaseOrderStatus,
    ReturnOrder, ReturnOrderLineItem, ReturnOrderStatus,
    SalesOrder, SalesOrderLineItem, SalesOrderShipment, SalesOrderStatus,
)
logger = logging.getLogger(__name__)


def _apply_line_location_scope_filter(queryset, *, profile_id, location_id, relation_field: str):
    scoped_location_ids = get_location_scope_ids(profile_id=profile_id, stock_location_id=location_id)
    if not scoped_location_ids:
        return queryset.none()
    return queryset.filter(**{f"{relation_field}__in": scoped_location_ids})


def _apply_line_location_scope_filters(queryset, *, profile_id, location_ids, relation_field: str):
    scoped_location_ids = get_location_scope_ids_for_locations(profile_id=profile_id, stock_location_ids=location_ids)
    if not scoped_location_ids:
        return queryset.none()
    return queryset.filter(**{f"{relation_field}__in": scoped_location_ids})


def _resolve_structural_scope_location_id(request, payload=None):
    payload = payload or {}
    return (
        payload.get('structural_location_id')
        or request.query_params.get('structural_location_id')
        or request.query_params.get('stock_location')
    )


def _resolve_structural_scope_location_ids(request):
    raw_ids: list[str] = []
    for key in ('structural_location_id', 'stock_location'):
        value = request.query_params.get(key)
        if value:
            raw_ids.append(value)
    for key in ('structural_location_ids', 'stock_location_ids'):
        raw_ids.extend(request.query_params.getlist(key))
        csv_value = request.query_params.get(key)
        if csv_value:
            raw_ids.extend([part.strip() for part in csv_value.split(',') if part.strip()])
    return raw_ids


def _resolve_scope_mode(request):
    value = (request.query_params.get('scope') or '').strip().lower()
    if value == 'all':
        return 'all_locations'
    return value


def _assert_location_within_scope(*, profile_id, structural_scope_location_id, stock_location, label: str):
    if not structural_scope_location_id:
        return
    scoped_location_ids = get_location_scope_ids(
        profile_id=profile_id,
        stock_location_id=structural_scope_location_id,
    )
    if not scoped_location_ids:
        raise ValueError("The selected structural location scope is unavailable.")
    if stock_location is None or stock_location.id not in scoped_location_ids:
        raise ValueError(f"{label} is outside the selected structural location scope.")


def _assert_reservation_within_scope(*, profile_id, structural_scope_location_id, reservation, label: str):
    if reservation is None:
        raise ValueError(f"{label} not found")
    _assert_location_within_scope(
        profile_id=profile_id,
        structural_scope_location_id=structural_scope_location_id,
        stock_location=reservation.stock_location,
        label=label,
    )


def _audit_actor_from_request(request):
    user_id = get_request_user_id(request, as_str=False)
    profile_id = get_request_profile_id(request, as_str=False)
    return {
        'user_id': str(user_id or ''),
        'profile_id': str(profile_id or ''),
    }


_POST_ISSUE_WORKFLOW_STATES = {
    'SENT_TO_SUPPLIER',
    'PARTIALLY_RECEIVED',
    'FULLY_RECEIVED',
    'CLOSED',
}


def _purchase_order_edit_lock_reason(purchase_order):
    if purchase_order is None:
        return None
    if purchase_order.status in {
        PurchaseOrderStatus.ISSUED,
        PurchaseOrderStatus.RECEIVED,
        PurchaseOrderStatus.COMPLETED,
        PurchaseOrderStatus.CANCELLED,
        PurchaseOrderStatus.RETURNED,
        PurchaseOrderStatus.REJECTED,
        PurchaseOrderStatus.LOST,
        PurchaseOrderStatus.OVERDUE,
    }:
        return 'Issued purchase orders can no longer be edited.'
    if purchase_order.workflow_state in _POST_ISSUE_WORKFLOW_STATES:
        return 'Issued purchase orders can no longer be edited.'
    if purchase_order.issue_date:
        return 'Issued purchase orders can no longer be edited.'
    return None


def _sales_order_edit_lock_reason(sales_order):
    if sales_order is None:
        return None
    if sales_order.status in {SalesOrderStatus.COMPLETED, SalesOrderStatus.CANCELLED}:
        return 'Completed or cancelled sales orders can no longer be edited.'
    return None


def _purchase_order_open_line_count(purchase_order):
    line_items = getattr(purchase_order, 'line_items', None)
    if line_items is None:
        return 0
    if hasattr(line_items, 'filter'):
        return line_items.filter(fully_received=False).count()
    try:
        return sum(1 for line in line_items.all() if not getattr(line, 'fully_received', False))
    except Exception:
        return 0


class GoodsReceiptViewSet(BaseCachePermissionViewset):
    # Receipts are posted through purchase-order workflows, so this viewset
    # cannot reliably invalidate a five-minute list cache on every mutation.
    CACHE_ENABLED = False
    required_permission = UNIFIED_PERMISSION_DICT.get('purchase_order')
    queryset = GoodsReceipt.objects.select_related('purchase_order', 'supplier').prefetch_related(
        Prefetch(
            'lines',
            queryset=GoodsReceiptLine.objects.select_related(
                'inventory_item',
                'stock_location',
                'stock_location__parent',
                'stock_location__parent__parent',
                'stock_location__parent__parent__parent',
            ),
        ),
    )
    filterset_fields = ['purchase_order', 'supplier']
    search_fields = [
        'reference',
        'purchase_order__reference',
        'supplier__name',
        'notes',
        'lines__inventory_item__name_snapshot',
        'lines__stock_location__name',
        'lines__lot_number',
    ]
    ordering_fields = ['received_at', 'created_at', 'reference']
    ordering = ['-received_at', '-created_at']
    http_method_names = ['get', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'list':
            return GoodsReceiptListSerializer
        return GoodsReceiptDetailSerializer

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

        scope_location_ids = _resolve_structural_scope_location_ids(self.request)
        if profile_id and _resolve_scope_mode(self.request) != 'all_locations' and scope_location_ids:
            queryset = _apply_line_location_scope_filters(
                queryset,
                profile_id=profile_id,
                location_ids=scope_location_ids,
                relation_field='lines__stock_location_id',
            )

        inventory_item_id = self.request.query_params.get('inventory_item')
        if inventory_item_id:
            queryset = queryset.filter(lines__inventory_item_id=inventory_item_id)

        date_from = self.request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(received_at__date__gte=date_from)

        date_to = self.request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(received_at__date__lte=date_to)

        return queryset.distinct()

    @action(detail=False, methods=['get'])
    def summary(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        aggregates = queryset.aggregate(total_quantity=Sum('lines__received_quantity'))
        return Response(
            {
                'total_receipts': queryset.count(),
                'total_quantity': aggregates.get('total_quantity') or Decimal('0'),
                'supplier_count': queryset.values('supplier_id').distinct().count(),
                'purchase_order_count': queryset.values('purchase_order_id').distinct().count(),
                'location_count': queryset.values('lines__stock_location_id').distinct().count(),
                'inventory_item_count': queryset.values('lines__inventory_item_id').distinct().count(),
            }
        )


class SalesOrderShipmentViewSet(BaseCachePermissionViewset):
    required_permission = UNIFIED_PERMISSION_DICT.get('sales_order')
    queryset = SalesOrderShipment.objects.select_related(
        'order',
        'order__customer',
    ).prefetch_related(
        'lines',
        'lines__sales_order_line',
        'lines__sales_order_line__inventory_item',
        'lines__stock_location',
        'lines__stock_lot',
        'lines__stock_serial',
        'lines__reservation',
    )
    filterset_fields = ['order', 'order__customer', 'shipment_date', 'delivery_date']
    search_fields = [
        'reference',
        'order__reference',
        'order__customer__name',
        'tracking_number',
        'invoice_number',
        'notes',
        'lines__sales_order_line__inventory_item__name_snapshot',
        'lines__stock_location__name',
        'lines__stock_lot__lot_number',
        'lines__stock_serial__serial_number',
    ]
    ordering_fields = ['shipment_date', 'delivery_date', 'created_at', 'reference']
    ordering = ['-shipment_date', '-created_at']
    http_method_names = ['get', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'list':
            return SalesOrderShipmentListSerializer
        return SalesOrderShipmentDetailSerializer

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

        order_id = self.request.query_params.get('order')
        if order_id:
            queryset = queryset.filter(order_id=order_id)

        customer_id = self.request.query_params.get('customer')
        if customer_id:
            queryset = queryset.filter(order__customer_id=customer_id)

        scope_location_ids = _resolve_structural_scope_location_ids(self.request)
        if profile_id and _resolve_scope_mode(self.request) != 'all_locations' and scope_location_ids:
            queryset = _apply_line_location_scope_filters(
                queryset,
                profile_id=profile_id,
                location_ids=scope_location_ids,
                relation_field='lines__stock_location_id',
            )

        inventory_item_id = self.request.query_params.get('inventory_item')
        if inventory_item_id:
            queryset = queryset.filter(lines__sales_order_line__inventory_item_id=inventory_item_id)

        date_from = self.request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(shipment_date__gte=date_from)

        date_to = self.request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(shipment_date__lte=date_to)

        return queryset.distinct()

    @action(detail=False, methods=['get'])
    def summary(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        aggregates = queryset.aggregate(total_quantity=Sum('lines__quantity_shipped'))
        return Response(
            {
                'total_shipments': queryset.count(),
                'total_quantity': aggregates.get('total_quantity') or Decimal('0'),
                'tracked_shipment_count': queryset.exclude(
                    Q(tracking_number='') & Q(invoice_number='') & Q(link='')
                ).count(),
                'customer_count': queryset.values('order__customer_id').distinct().count(),
                'order_count': queryset.values('order_id').distinct().count(),
                'location_count': queryset.values('lines__stock_location_id').distinct().count(),
                'inventory_item_count': queryset.values('lines__sales_order_line__inventory_item_id').distinct().count(),
            }
        )

class PurchaseOrderViewSet(BaseCachePermissionViewset):
    """
    Enhanced ViewSet for comprehensive purchase order management
    Includes workflow management, receiving, returns, and analytics
    """
    # Purchase-order state changes through custom line-item and workflow actions.
    # The generic five-minute cache is not invalidated by those actions and can
    # otherwise hide newly added lines or receipts from the operations workbench.
    CACHE_ENABLED = False
    required_permission = UNIFIED_PERMISSION_DICT.get('purchase_order')
    pagination_class = PurchaseOrderPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    queryset = PurchaseOrder.objects.select_related('supplier', 'contact', 'address').prefetch_related('line_items')
    # permission_classes = [IsAuthenticated, HasModelRequestPermission]
    
    filterset_fields = ['status', 'supplier', 'issue_date', 'delivery_date']
    search_fields = ['reference', 'description', 'supplier_reference', 'supplier__name']
    ordering_fields = ['reference', 'issue_date', 'delivery_date', 'created_at']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return PurchaseOrderListSerializer
        return PurchaseOrderDetailSerializer
    
    def get_queryset(self):
        """Filter by profile and add custom filters"""
        queryset = super().get_queryset()
        profile_id = get_request_profile_id(self.request, as_str=False)
        if profile_id:
            queryset = scope_queryset_by_identity(
                queryset,
                canonical_field='profile_id',
                legacy_field='profile',
                value=profile_id,
            )
        
        # Additional filters
        status_filter = self.request.query_params.get('status_filter')
        if status_filter == 'active':
            queryset = queryset.exclude(status__in=['completed', 'cancelled'])
        elif status_filter == 'overdue':
            queryset = queryset.filter(
                delivery_date__lt=timezone.now().date(),
                status__in=['issued', 'approved']
            )
        elif status_filter == 'pending_approval':
            queryset = queryset.filter(status='pending')
        
        # Date range filters
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)

        delivery_date_from = self.request.query_params.get('delivery_date_from')
        delivery_date_to = self.request.query_params.get('delivery_date_to')
        if delivery_date_from:
            queryset = queryset.filter(delivery_date__gte=delivery_date_from)
        if delivery_date_to:
            queryset = queryset.filter(delivery_date__lte=delivery_date_to)
        return queryset
    
    def perform_create(self, serializer):
        """Set created_by and profile on creation"""
        current_user_id = get_request_user_id(self.request, as_str=False)
        profile_id = get_request_profile_id(self.request, required=True, as_str=False)
        
        extra_fields = {
            'status': PurchaseOrderStatus.PENDING,
        }
        
        if current_user_id:
            extra_fields['created_by_user_id'] = current_user_id
            extra_fields['responsible_user_id'] = current_user_id
        extra_fields['profile_id'] = profile_id
            
        instance = serializer.save(**extra_fields)
        payload = serialize_purchase_order(instance)
        publish_order_admin_event(
            event_name='purchase_order.created',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'purchase_order',
                'id': str(instance.id),
                'label': instance.reference,
            },
            summary=f'Purchase order created: {instance.reference}.',
            metadata={
                'status': instance.status,
                'supplier_id': str(instance.supplier_id or ''),
                'supplier_name': getattr(instance.supplier, 'name', '') or '',
            },
            after=payload,
            feature_area='purchasing',
            reference_number=instance.reference,
            notification_category='purchase_order',
            notification_title=f'Purchase order {instance.reference} created',
            notification_message=(
                f"Purchase order {instance.reference} was created"
                f"{f' for {getattr(instance.supplier, 'name', '')}' if getattr(instance.supplier, 'name', '') else ''}."
            ),
            notification_action_url='/order/purchase',
        )
        
        # Log activity
        self._log_activity('CREATE', instance, {
            'initial_data': self.request.data,
            'created_data': serializer.data
        })
    
    def perform_update(self, serializer):
        """Set modified_by on update and log changes"""
        current_user_id = get_request_user_id(self.request, as_str=False)
        before = serialize_purchase_order(serializer.instance)
        original_data = self.get_serializer(serializer.instance).data
        
        extra_fields = {}
        if current_user_id:
            extra_fields['updated_by_user_id'] = current_user_id
        
        instance = serializer.save(**extra_fields)
        after = serialize_purchase_order(instance)
        publish_order_admin_event(
            event_name='purchase_order.updated',
            payload=after,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'purchase_order',
                'id': str(instance.id),
                'label': instance.reference,
            },
            summary=f'Purchase order updated: {instance.reference}.',
            metadata={
                'status': instance.status,
                'updated_fields': list(serializer.validated_data.keys()),
            },
            before=before,
            after=after,
            feature_area='purchasing',
            reference_number=instance.reference,
        )
        
        # Log activity
        self._log_activity('UPDATE', instance, {
            'changes': self._get_field_changes(original_data, serializer.data)
        })

    def update(self, request, *args, **kwargs):
        purchase_order = self.get_object()
        lock_reason = _purchase_order_edit_lock_reason(purchase_order)
        if lock_reason:
            return Response({'error': lock_reason}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        purchase_order = self.get_object()
        lock_reason = _purchase_order_edit_lock_reason(purchase_order)
        if lock_reason:
            return Response({'error': lock_reason}, status=status.HTTP_400_BAD_REQUEST)
        return super().partial_update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        before = serialize_purchase_order(instance)
        publish_order_admin_event(
            event_name='purchase_order.deleted',
            payload=before,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'purchase_order',
                'id': str(instance.id),
                'label': instance.reference,
            },
            summary=f'Purchase order deleted: {instance.reference}.',
            metadata={'status': instance.status},
            before=before,
            after={},
            severity='warning',
            feature_area='purchasing',
            reference_number=instance.reference,
        )
        instance.delete()
    
    # ==================== LINE ITEM MANAGEMENT ====================
    @action(detail=True, methods=['get'])
    def line_items(self, request, pk=None):
        """List line items for purchase order"""    
        purchase_order = self.get_object()
        line_items = purchase_order.line_items.all()
        serializer = PurchaseOrderLineItemSerializer(line_items, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def add_line_item(self, request, pk=None):
        """Add line item to purchase order"""
        purchase_order = self.get_object()
        lock_reason = _purchase_order_edit_lock_reason(purchase_order)
        if lock_reason:
            return Response({'error': lock_reason}, status=status.HTTP_400_BAD_REQUEST)
        if purchase_order.status not in [PurchaseOrderStatus.PENDING,]:
            return Response(
                {'error': 'Cannot add line item to order in current status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if purchase_order.status not in [PurchaseOrderStatus.PENDING, 'draft']:
            return Response(
                {'error': 'Cannot modify order in current status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = PurchaseOrderLineItemCreateSerializer(data=request.data)
        if serializer.is_valid():
            line_item = serializer.save(purchase_order=purchase_order)
            payload = serialize_purchase_order_line_item(line_item)
            publish_order_admin_event(
                event_name='purchase_order.line_item.added',
                payload=payload,
                actor=_audit_actor_from_request(request),
                target={
                    'type': 'purchase_order_line_item',
                    'id': str(line_item.id),
                    'label': f'{purchase_order.reference} / {line_item.inventory_item.name_snapshot}',
                    'barcode': line_item.inventory_item.barcode_snapshot or '',
                    'sku': line_item.inventory_item.sku_snapshot or '',
                },
                summary=f'Line item added to purchase order {purchase_order.reference}.',
                metadata={
                    'purchase_order_id': str(purchase_order.id),
                    'purchase_order_reference': purchase_order.reference,
                },
                after=payload,
                feature_area='purchasing',
                reference_number=purchase_order.reference,
            )
            
            # Recalculate order total
            
            # Log activity
            # self._log_activity('ADD_LINE_ITEM', purchase_order, {
            #     'line_item_id': line_item.id,
            #     'quantity': line_item.quantity,
            #     'unit_price': str(line_item.unit_price)
            # })
            
            return Response(
                PurchaseOrderLineItemSerializer(line_item).data,
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['put', 'patch'])
    def update_line_item(self, request, pk=None):
        """Update a specific line item"""
        purchase_order = self.get_object()
        lock_reason = _purchase_order_edit_lock_reason(purchase_order)
        if lock_reason:
            return Response({'error': lock_reason}, status=status.HTTP_400_BAD_REQUEST)
        if purchase_order.status not in [PurchaseOrderStatus.PENDING,]:
            return Response(
                {'error': f'Cannot edit line item in the  order in current status ({purchase_order.status})'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        line_item_id = request.data.get('line_item_id')
        
        if not line_item_id:
            return Response(
                {'error': 'line_item_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            line_item = purchase_order.line_items.get(id=line_item_id)
        except PurchaseOrderLineItem.DoesNotExist:
            return Response(
                {'error': 'Line item not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if order can be modified
        if purchase_order.status not in [PurchaseOrderStatus.PENDING, 'draft']:
            return Response(
                {'error': 'Cannot modify order in current status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = PurchaseOrderLineItemCreateSerializer(
            line_item, data=request.data, partial=True
        )
        if serializer.is_valid():
            before = serialize_purchase_order_line_item(line_item)
            line_item = serializer.save()
            after = serialize_purchase_order_line_item(line_item)
            publish_order_admin_event(
                event_name='purchase_order.line_item.updated',
                payload=after,
                actor=_audit_actor_from_request(request),
                target={
                    'type': 'purchase_order_line_item',
                    'id': str(line_item.id),
                    'label': f'{purchase_order.reference} / {line_item.inventory_item.name_snapshot}',
                    'barcode': line_item.inventory_item.barcode_snapshot or '',
                    'sku': line_item.inventory_item.sku_snapshot or '',
                },
                summary=f'Line item updated on purchase order {purchase_order.reference}.',
                metadata={
                    'purchase_order_id': str(purchase_order.id),
                    'purchase_order_reference': purchase_order.reference,
                },
                before=before,
                after=after,
                feature_area='purchasing',
                reference_number=purchase_order.reference,
            )
            
            # Recalculate order total
            
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['delete'])
    def remove_line_item(self, request, pk=None):
        """Remove a line item from purchase order"""
        purchase_order = self.get_object()
        lock_reason = _purchase_order_edit_lock_reason(purchase_order)
        if lock_reason:
            return Response({'error': lock_reason}, status=status.HTTP_400_BAD_REQUEST)
        line_item_id = request.query_params.get('line_item_id')
        
        if not line_item_id:
            return Response(
                {'error': 'line_item_id parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            line_item = purchase_order.line_items.get(id=line_item_id)
        except PurchaseOrderLineItem.DoesNotExist:
            return Response(
                {'error': 'Line item not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if order can be modified
        if purchase_order.status not in [PurchaseOrderStatus.PENDING, 'draft']:
            return Response(
                {'error': 'Cannot modify order in current status'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        before = serialize_purchase_order_line_item(line_item)
        publish_order_admin_event(
            event_name='purchase_order.line_item.removed',
            payload=before,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'purchase_order_line_item',
                'id': str(line_item.id),
                'label': f'{purchase_order.reference} / {line_item.inventory_item.name_snapshot}',
                'barcode': line_item.inventory_item.barcode_snapshot or '',
                'sku': line_item.inventory_item.sku_snapshot or '',
            },
            summary=f'Line item removed from purchase order {purchase_order.reference}.',
            metadata={
                'purchase_order_id': str(purchase_order.id),
                'purchase_order_reference': purchase_order.reference,
            },
            before=before,
            after={},
            severity='warning',
            feature_area='purchasing',
            reference_number=purchase_order.reference,
        )
        line_item.delete()
        
        # Recalculate order total
        
        # Log activity
        self._log_activity('REMOVE_LINE_ITEM', purchase_order, {
            'removed_line_item_id': line_item_id
        })
        
        return Response({'message': 'Line item removed successfully'})
    
    # ==================== WORKFLOW MANAGEMENT ====================
    
    @action(detail=True, methods=['put', 'patch'])
    def approve(self, request, pk=None):
        """Approve purchase order"""
        purchase_order = self.get_object()
        
        if purchase_order.status not in [PurchaseOrderStatus.PENDING,'draft']:
            return Response(
                {'error': 'Only pending orders can be approved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        
        with transaction.atomic():
            before = serialize_purchase_order(purchase_order)
            purchase_order.status = PurchaseOrderStatus.APPROVED
            purchase_order.approved_by_user_id = get_request_user_id(request, required=True, as_str=False)
            purchase_order.approved_at = timezone.now()
            purchase_order.save()
            after = serialize_purchase_order(purchase_order)
            publish_order_admin_event(
                event_name='purchase_order.approved',
                payload=after,
                actor=_audit_actor_from_request(request),
                target={
                    'type': 'purchase_order',
                    'id': str(purchase_order.id),
                    'label': purchase_order.reference,
                },
                summary=f'Purchase order approved: {purchase_order.reference}.',
                metadata={'status': purchase_order.status},
                before=before,
                after=after,
                feature_area='purchasing',
                reference_number=purchase_order.reference,
                notification_category='approval_required',
                notification_title=f'Purchase order {purchase_order.reference} approved',
                notification_message=f'Purchase order {purchase_order.reference} was approved and is ready for issue.',
                notification_action_url='/order/purchase',
            )
            
            # # Log activity
            # self._log_activity('APPROVE', purchase_order, {
            #     'approved_by': current_user.get('full_name'),
            #     'notes': serializer.validated_data.get('notes', '')
            # })
        
        return Response({
            'message': 'Purchase order approved successfully',
            'status': purchase_order.status,
            'approved_at': purchase_order.approved_at
        })
    
    
    @action(detail=True, methods=['put', 'patch'])
    def issue(self, request, pk=None):
        """Enhanced issue method with proper email handling"""
        purchase_order = self.get_object()
        
        if purchase_order.status != PurchaseOrderStatus.APPROVED:
            return Response(
                {'error': 'Only approved orders can be issued'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        
        current_user = IdentityDirectory.get_current_user(request) or {}
        current_user_id = current_user.get('id')
        
        try:
            with transaction.atomic():
                before = serialize_purchase_order(purchase_order)
                # Calculate total price
                total_price =purchase_order.total_price
                
                purchase_order.status = PurchaseOrderStatus.ISSUED
                purchase_order.issue_date = timezone.now()
                purchase_order.updated_by_user_id = current_user_id
                purchase_order.save()
                after = serialize_purchase_order(purchase_order)
                
                # Send email notification if requested
                email_sent = False
                if request.data.get('notify_supplier', True):
                    try:
                        self._send_purchase_order_email(
                            purchase_order,
                            delivery_key=f"purchase-order:{purchase_order.id}:issued:{purchase_order.issue_date.isoformat()}",
                        )
                        email_sent = True
                    except Exception as e:
                        logger.warning(f"Failed to send email for PO {purchase_order.reference}: {str(e)}")
                        # Don't fail the entire operation if email fails
                publish_order_admin_event(
                    event_name='purchase_order.issued',
                    payload=after,
                    actor=_audit_actor_from_request(request),
                    target={
                        'type': 'purchase_order',
                        'id': str(purchase_order.id),
                        'label': purchase_order.reference,
                    },
                    summary=f'Purchase order issued: {purchase_order.reference}.',
                    metadata={
                        'status': purchase_order.status,
                        'email_sent': email_sent,
                        'notify_supplier': bool(request.data.get('notify_supplier', True)),
                        'total_price': str(total_price),
                    },
                    before=before,
                    after=after,
                    feature_area='purchasing',
                    reference_number=purchase_order.reference,
                    notification_category='purchase_order',
                    notification_title=f'Purchase order {purchase_order.reference} issued',
                    notification_message=f'Purchase order {purchase_order.reference} was issued to the supplier.',
                    notification_action_url='/order/purchase',
                )
                
                # # Log activity
                # self._log_activity('ISSUE', purchase_order, {
                #     'issued_by': current_user.get('full_name'),
                #     'total_price': str(total_price),
                #     'email_sent': email_sent,
                #     'email_requested': serializer.validated_data.get('notify_supplier', True)
                # })
            
            return Response({
                'message': 'Purchase order issued successfully',
                'status': purchase_order.status,
                'total_price': total_price,
                'issue_date': purchase_order.issue_date,
                'email_sent': email_sent
            })
            
        except Exception as e:
            logger.error(f"Error issuing purchase order {purchase_order.reference}: {str(e)}")
            return Response(
                {'error': f'Error issuing purchase order: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['put', 'patch'])
    def receive(self, request, pk=None):
        """Mark purchase order as received"""
        purchase_order = self.get_object()
        
        if purchase_order.status != PurchaseOrderStatus.ISSUED:
            return Response(
                {'error': 'Only issued orders can be received'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        current_user = IdentityDirectory.get_current_user(request) or {}
        current_user_id = current_user.get('id')
        
        with transaction.atomic():
            before = serialize_purchase_order(purchase_order)
            purchase_order.status = PurchaseOrderStatus.RECEIVED
            purchase_order.received_date = timezone.now()
            purchase_order.received_by_user_id = current_user_id
            purchase_order.save()
            after = serialize_purchase_order(purchase_order)
            publish_order_admin_event(
                event_name='purchase_order.received',
                payload=after,
                actor=_audit_actor_from_request(request),
                target={
                    'type': 'purchase_order',
                    'id': str(purchase_order.id),
                    'label': purchase_order.reference,
                },
                summary=f'Purchase order received: {purchase_order.reference}.',
                metadata={'status': purchase_order.status},
                before=before,
                after=after,
                feature_area='purchasing',
                reference_number=purchase_order.reference,
                notification_category='purchase_order',
                notification_title=f'Purchase order {purchase_order.reference} received',
                notification_message=f'Purchase order {purchase_order.reference} was marked received.',
                notification_action_url='/order/purchase',
            )
            
            # # Log activity
            # self._log_activity('RECEIVE', purchase_order, {
            #     'received_by': current_user.get('full_name'),
            #     'received_date': purchase_order.received_date
            # })
        
        return Response({
            'message': 'Purchase order marked as received',
            'status': purchase_order.status,
            'received_date': purchase_order.received_date
        })
    
    @action(detail=True, methods=['put', 'patch'])
    def receive_items(self, request, pk=None):
        """Receive specific items and update stock"""
        purchase_order = self.get_object()
        
        if purchase_order.status not in [PurchaseOrderStatus.ISSUED, PurchaseOrderStatus.RECEIVED]:
            return Response(
                {'error': 'Order must be issued or received to receive items'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = ReceiveItemsSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        received_items = serializer.validated_data['received_items']
        current_user = IdentityDirectory.get_current_user(request) or {}
        current_user_id = get_request_user_id(request, as_str=False)
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        structural_scope_location_id = _resolve_structural_scope_location_id(request, serializer.validated_data)
        
        try:
            with transaction.atomic():
                before = serialize_purchase_order(purchase_order)
                actor = _audit_actor_from_request(request)
                goods_receipt = StockDomainService.create_goods_receipt(
                    purchase_order=purchase_order,
                    actor_user_id=current_user_id,
                    notes=request.data.get('notes', ''),
                )
                received_count = 0
                receipt_line_summaries = []
                total_quantity_received = Decimal('0')
                total_serials_received = 0
                touched_location_ids: set[str] = set()
                touched_inventory_item_ids: set[str] = set()
                fully_received_line_count = 0
                partially_received_line_count = 0
                
                for item_data in received_items:
                    line_item_id = item_data['line_item_id']
                    quantity_received = item_data['quantity_received']
                    location_id = item_data['location_id']
                    
                    # Get line item
                    try:
                        line_item = purchase_order.line_items.get(id=line_item_id)
                    except PurchaseOrderLineItem.DoesNotExist:
                        raise ValueError(f"Line item {line_item_id} not found")
                    before_line = serialize_purchase_order_line_item(line_item)
                    stock_location = scope_queryset_by_identity(
                        StockLocation.objects.filter(id=location_id),
                        canonical_field='profile_id',
                        legacy_field='profile',
                        value=profile_id,
                    ).first()
                    if stock_location is None:
                        raise ValueError(f"Stock location {location_id} not found")
                    _assert_location_within_scope(
                        profile_id=profile_id,
                        structural_scope_location_id=structural_scope_location_id,
                        stock_location=stock_location,
                        label=f"Receiving location {location_id}",
                    )

                    receipt_result = StockDomainService.receive_purchase_line(
                        purchase_order=purchase_order,
                        line_item=line_item,
                        stock_location=stock_location,
                        quantity_received=quantity_received,
                        unit_cost=item_data.get('unit_cost'),
                        actor_user_id=current_user_id,
                        goods_receipt=goods_receipt,
                        lot_number=item_data.get('lot_number', ''),
                        manufactured_date=item_data.get('manufactured_date'),
                        expiry_date=item_data.get('expiry_date'),
                        serial_numbers=item_data.get('serial_numbers'),
                        notes=item_data.get('notes') or request.data.get('notes', ''),
                    )
                    line_item.refresh_from_db()
                    after_line = serialize_purchase_order_line_item(line_item)
                    goods_receipt_line = receipt_result['goods_receipt_line']
                    goods_receipt_line_payload = serialize_goods_receipt_line(goods_receipt_line)
                    stock_lot = receipt_result.get('stock_lot')
                    stock_serials = receipt_result.get('stock_serials') or []
                    balance = receipt_result.get('balance')
                    serial_numbers = [serial.serial_number for serial in stock_serials]
                    is_fully_received = bool(goods_receipt_line_payload.get('fully_received'))
                    line_receipt_state = 'complete' if is_fully_received else 'partial'
                    line_event_metadata = {
                        'purchase_order_id': str(purchase_order.id),
                        'purchase_order_reference': purchase_order.reference,
                        'goods_receipt_id': str(goods_receipt.id),
                        'goods_receipt_reference': goods_receipt.reference,
                        'stock_location_id': str(stock_location.id),
                        'stock_location_name': stock_location.name,
                        'inventory_item_id': str(line_item.inventory_item_id),
                        'inventory_name': line_item.inventory_item.name_snapshot,
                        'inventory_sku': line_item.inventory_item.sku_snapshot or '',
                        'inventory_barcode': line_item.inventory_item.barcode_snapshot or '',
                        'product_variant_image_url': line_item.inventory_item.product_variant_image_url or '',
                        'quantity_received': str(quantity_received),
                        'quantity_received_to_date': goods_receipt_line_payload.get('quantity_received_to_date'),
                        'remaining_quantity': goods_receipt_line_payload.get('remaining_quantity'),
                        'fully_received': goods_receipt_line_payload.get('fully_received'),
                        'receipt_completion_state': line_receipt_state,
                        'lot_number': goods_receipt_line_payload.get('lot_number', ''),
                        'stock_lot_id': str(getattr(stock_lot, 'id', '') or ''),
                        'serial_count': len(serial_numbers),
                        'serial_numbers': serial_numbers,
                        'balance_quantity_on_hand': str(getattr(balance, 'quantity_on_hand', '')),
                    }
                    receipt_line_summaries.append({
                        **line_event_metadata,
                        'goods_receipt_line_id': str(goods_receipt_line.id),
                    })
                    publish_order_admin_event(
                        event_name='goods_receipt.line.received',
                        payload=goods_receipt_line_payload,
                        actor=actor,
                        target={
                            'type': 'goods_receipt_line',
                            'id': str(goods_receipt_line.id),
                            'label': f'{goods_receipt.reference} / {line_item.inventory_item.name_snapshot}',
                            'barcode': line_item.inventory_item.barcode_snapshot or '',
                            'sku': line_item.inventory_item.sku_snapshot or '',
                        },
                        summary=(
                            f'{"Fully" if is_fully_received else "Partially"} received {quantity_received} of {line_item.inventory_item.name_snapshot} '
                            f'into {stock_location.name} on {goods_receipt.reference}.'
                        ),
                        metadata=line_event_metadata,
                        before=before_line,
                        after=goods_receipt_line_payload,
                        feature_area='goods_receipts',
                        reference_number=goods_receipt.reference,
                    )
                    
                    received_count += 1
                    total_quantity_received += Decimal(str(quantity_received))
                    total_serials_received += len(serial_numbers)
                    touched_location_ids.add(str(stock_location.id))
                    touched_inventory_item_ids.add(str(line_item.inventory_item_id))
                    if is_fully_received:
                        fully_received_line_count += 1
                    else:
                        partially_received_line_count += 1
                
                # Update order status if not already received
                status_transitioned_to_received = False
                if purchase_order.status == PurchaseOrderStatus.ISSUED:
                    purchase_order.status = PurchaseOrderStatus.RECEIVED
                    purchase_order.received_date = timezone.now()
                    purchase_order.received_by_user_id = current_user_id
                    purchase_order.save()
                    status_transitioned_to_received = True
                after = serialize_purchase_order(purchase_order)
                goods_receipt_payload = serialize_goods_receipt(goods_receipt)
                open_line_count = _purchase_order_open_line_count(purchase_order)
                receipt_completion_state = 'complete' if open_line_count == 0 else 'partial'
                if status_transitioned_to_received:
                    publish_order_admin_event(
                        event_name='purchase_order.received',
                        payload=after,
                        actor=actor,
                        target={
                            'type': 'purchase_order',
                            'id': str(purchase_order.id),
                            'label': purchase_order.reference,
                        },
                        summary=f'Purchase order received: {purchase_order.reference}.',
                        metadata={
                            'status': purchase_order.status,
                            'goods_receipt_id': str(goods_receipt.id),
                            'goods_receipt_reference': goods_receipt.reference,
                            'received_count': received_count,
                            'total_quantity_received': str(total_quantity_received),
                            'receipt_completion_state': receipt_completion_state,
                            'open_line_count': open_line_count,
                            'fully_received_line_count': fully_received_line_count,
                            'partially_received_line_count': partially_received_line_count,
                        },
                        before=before,
                        after=after,
                        feature_area='purchasing',
                        reference_number=purchase_order.reference,
                        notification_category='purchase_order',
                        notification_title=f'Purchase order {purchase_order.reference} received',
                        notification_message=(
                            f'Purchase order {purchase_order.reference} was marked received through goods receipt {goods_receipt.reference}.'
                        ),
                        notification_action_url='/order/purchase',
                    )
                publish_order_admin_event(
                    event_name='goods_receipt.created',
                    payload=goods_receipt_payload,
                    actor=actor,
                    target={
                        'type': 'goods_receipt',
                        'id': str(goods_receipt.id),
                        'label': goods_receipt.reference,
                    },
                    summary=f'Goods receipt created: {goods_receipt.reference}.',
                    metadata={
                        'purchase_order_id': str(purchase_order.id),
                        'purchase_order_reference': purchase_order.reference,
                        'received_count': received_count,
                        'total_quantity_received': str(total_quantity_received),
                        'total_serials_received': total_serials_received,
                        'receipt_completion_state': receipt_completion_state,
                        'open_line_count': open_line_count,
                        'fully_received_line_count': fully_received_line_count,
                        'partially_received_line_count': partially_received_line_count,
                        'receipt_lines': receipt_line_summaries,
                    },
                    after=goods_receipt_payload,
                    feature_area='goods_receipts',
                    reference_number=goods_receipt.reference,
                    notification_category='purchase_order',
                    notification_title=(
                        f'{"Complete" if receipt_completion_state == "complete" else "Partial"} goods receipt {goods_receipt.reference} posted'
                    ),
                    notification_message=(
                        f'Goods receipt {goods_receipt.reference} was posted for purchase order {purchase_order.reference} '
                        f'with a {receipt_completion_state} receiving state.'
                    ),
                    notification_action_url='/order/purchase',
                )
                publish_order_admin_event(
                    event_name='purchase_order.items_received',
                    payload=after,
                    actor=actor,
                    target={
                        'type': 'purchase_order',
                        'id': str(purchase_order.id),
                        'label': purchase_order.reference,
                    },
                    summary=f'Items received for purchase order {purchase_order.reference}.',
                    metadata={
                        'goods_receipt_id': str(goods_receipt.id),
                        'goods_receipt_reference': goods_receipt.reference,
                        'received_count': received_count,
                        'total_quantity_received': str(total_quantity_received),
                        'total_serials_received': total_serials_received,
                        'receipt_completion_state': receipt_completion_state,
                        'open_line_count': open_line_count,
                        'fully_received_line_count': fully_received_line_count,
                        'partially_received_line_count': partially_received_line_count,
                        'location_ids': sorted(touched_location_ids),
                        'inventory_item_ids': sorted(touched_inventory_item_ids),
                        'receipt_lines': receipt_line_summaries,
                    },
                    before=before,
                    after=after,
                    feature_area='goods_receipts',
                    reference_number=purchase_order.reference,
                    notification_category='purchase_order',
                    notification_title=(
                        f'{"Complete" if receipt_completion_state == "complete" else "Partial"} receipt on {purchase_order.reference}'
                    ),
                    notification_message=(
                        f'{received_count} line item{"" if received_count == 1 else "s"} were received on '
                        f'purchase order {purchase_order.reference} with a {receipt_completion_state} receiving state.'
                    ),
                    notification_action_url='/order/purchase',
                )
                
                # Log activity
                self._log_activity('RECEIVE_ITEMS', purchase_order, {
                    'items_received': received_count,
                    'received_by': current_user.get('full_name'),
                    'items_detail': received_items
                })
                
                return Response({
                    'message': f'Successfully received {received_count} item types',
                    'received_count': received_count,
                    'order_status': purchase_order.status,
                    'goods_receipt_reference': goods_receipt.reference,
                })
                
        except StockDomainError as exc:
            logger.error(f"Stock domain error receiving items for PO {purchase_order.reference}: {str(exc)}")
            return Response(
                {'error': str(exc)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Error receiving items for PO {purchase_order.reference}: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['put', 'patch'])
    def complete(self, request, pk=None):
        """Mark purchase order as complete and finalize stock"""
        purchase_order = self.get_object()
        
        if purchase_order.status != PurchaseOrderStatus.RECEIVED:
            return Response(
                {'error': 'Only received orders can be completed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if _purchase_order_open_line_count(purchase_order) > 0:
            return Response(
                {'error': 'All purchase order line items must be fully received before completion'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        current_user = IdentityDirectory.get_current_user(request) or {}
        current_user_id = current_user.get('id')
        
        try:
            with transaction.atomic():
                before = serialize_purchase_order(purchase_order)
                purchase_order.status = PurchaseOrderStatus.COMPLETED
                purchase_order.complete_date = timezone.now()
                purchase_order.updated_by_user_id = current_user_id
                purchase_order.save()
                after = serialize_purchase_order(purchase_order)
                publish_order_admin_event(
                    event_name='purchase_order.completed',
                    payload=after,
                    actor=_audit_actor_from_request(request),
                    target={
                        'type': 'purchase_order',
                        'id': str(purchase_order.id),
                        'label': purchase_order.reference,
                    },
                    summary=f'Purchase order completed: {purchase_order.reference}.',
                    metadata={'status': purchase_order.status},
                    before=before,
                    after=after,
                    feature_area='purchasing',
                    reference_number=purchase_order.reference,
                    notification_category='purchase_order',
                    notification_title=f'Purchase order {purchase_order.reference} completed',
                    notification_message=f'Purchase order {purchase_order.reference} was completed.',
                    notification_action_url='/order/purchase',
                )
                
                # Log activity
                self._log_activity('COMPLETE', purchase_order, {
                    'completed_by': current_user.get('full_name'),
                    'completion_date': purchase_order.complete_date
                })
                
                return Response({
                    'message': 'Purchase order completed successfully',
                    'status': purchase_order.status,
                    'completion_date': purchase_order.complete_date
                })
                
        except Exception as e:
            logger.error(f"Error completing purchase order {purchase_order.reference}: {str(e)}")
            return Response(
                {'error': f'Error completing order: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['put', 'patch'])
    def cancel(self, request, pk=None):
        """Cancel purchase order"""
        purchase_order = self.get_object()
        
        if purchase_order.status in [PurchaseOrderStatus.COMPLETED, PurchaseOrderStatus.CANCELLED]:
            return Response(
                {'error': 'Cannot cancel completed or already cancelled orders'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        
        current_user = IdentityDirectory.get_current_user(request) or {}
        current_user_id = current_user.get('id')
        
        with transaction.atomic():
            before = serialize_purchase_order(purchase_order)
            purchase_order.status = PurchaseOrderStatus.CANCELLED
            purchase_order.updated_by_user_id = current_user_id
            purchase_order.notes = request.data.get('notes', purchase_order.notes)
            purchase_order.save()
            after = serialize_purchase_order(purchase_order)
            publish_order_admin_event(
                event_name='purchase_order.cancelled',
                payload=after,
                actor=_audit_actor_from_request(request),
                target={
                    'type': 'purchase_order',
                    'id': str(purchase_order.id),
                    'label': purchase_order.reference,
                },
                summary=f'Purchase order cancelled: {purchase_order.reference}.',
                metadata={'reason': request.data.get('notes', '')},
                before=before,
                after=after,
                severity='warning',
                feature_area='purchasing',
                reference_number=purchase_order.reference,
                notification_category='purchase_order',
                notification_title=f'Purchase order {purchase_order.reference} cancelled',
                notification_message=f'Purchase order {purchase_order.reference} was cancelled.',
                notification_action_url='/order/purchase',
            )
            
            # Log activity
            self._log_activity('CANCEL', purchase_order, {
                'cancelled_by': current_user.get('full_name'),
                'reason': request.data.get('notes', '')
            })
        
        return Response({
            'message': 'Purchase order cancelled successfully',
            'status': purchase_order.status,
            'cancelled_at': timezone.now()
        })
    
    # ==================== RETURN ORDER MANAGEMENT ====================
    
    @action(detail=True, methods=['post'])
    def create_return_order(self, request, pk=None):
        """Create return order from purchase order"""
        purchase_order = self.get_object()
        
        if purchase_order.status not in [PurchaseOrderStatus.RECEIVED, PurchaseOrderStatus.COMPLETED]:
            return Response(
                {'error': 'Can only create returns for received or completed orders'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = ReturnOrderCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        return_items = serializer.validated_data['return_items']
        return_reason = serializer.validated_data.get('return_reason', '')
        current_user = IdentityDirectory.get_current_user(request) or {}
        current_user_id = current_user.get('id')
        
        try:
            with transaction.atomic():
                # Create return order
                return_order = ReturnOrder.objects.create(
                    purchase_order=purchase_order,
                    profile_id=purchase_order.profile_id,
                    contact=purchase_order.contact,
                    address=purchase_order.address,
                    status=ReturnOrderStatus.PENDING,
                    return_reason=return_reason,
                    created_by_user_id=current_user_id,
                    responsible_user_id=current_user_id
                )
                
                # Create return line items
                for item in return_items:
                    try:
                        line_item = purchase_order.line_items.get(id=item['line_item_id'])
                    except PurchaseOrderLineItem.DoesNotExist:
                        raise ValueError(f"Line item {item['line_item_id']} not found")
                    
                    # Validate return quantity
                    previously_returned = line_item.returns.aggregate(
                        total=Sum('quantity_returned')
                    )['total'] or 0
                    returnable_quantity = Decimal(str(line_item.quantity_received)) - Decimal(str(previously_returned))
                    if Decimal(str(item['quantity'])) > returnable_quantity:
                        raise ValueError(
                            f"Cannot return {item['quantity']} items, "
                            f"only {returnable_quantity} remain returnable from received stock"
                        )
                    
                    ReturnOrderLineItem.objects.create(
                        return_order=return_order,
                        original_line_item=line_item,
                        quantity_returned=item['quantity'],
                        quantity_processed=0,
                        unit_price=line_item.unit_price,
                        tax_rate=line_item.tax_rate,
                        discount=line_item.discount,
                        return_reason=item.get('reason', '')
                    )
                return_order_payload = serialize_return_order(return_order)
                return_line_payloads = [
                    serialize_return_order_line_item(line) for line in return_order.line_items.select_related(
                        'original_line_item',
                        'original_line_item__inventory_item',
                        'return_order',
                    )
                ]
                publish_order_admin_event(
                    event_name='return_order.created',
                    payload=return_order_payload,
                    actor=_audit_actor_from_request(request),
                    target={
                        'type': 'return_order',
                        'id': str(return_order.id),
                        'label': return_order.reference,
                    },
                    summary=f'Return order created: {return_order.reference}.',
                    metadata={
                        'purchase_order_id': str(purchase_order.id),
                        'purchase_order_reference': purchase_order.reference,
                        'return_reason': return_reason,
                        'line_items': return_line_payloads,
                    },
                    after=return_order_payload,
                    feature_area='supplier_returns',
                    reference_number=return_order.reference,
                    notification_category='purchase_order',
                    notification_title=f'Return order {return_order.reference} created',
                    notification_message=(
                        f'Return order {return_order.reference} was created for purchase order {purchase_order.reference}.'
                    ),
                    notification_action_url='/order/purchase',
                )
                
                # Send notifications if requested
                try:
                    self._send_return_order_email(
                        return_order,
                        purchase_order,
                        delivery_key=f"return-order:{return_order.id}:created",
                    )
                except Exception as e:
                    logger.warning(f"Failed to send return order email: {str(e)}")
                
                # Log activity
                self._log_activity('CREATE_RETURN', purchase_order, {
                    'return_order_reference': return_order.reference,
                    'items_count': len(return_items),
                    'return_reason': return_reason
                })
                
                return Response({
                    'message': 'Return order created successfully',
                    'return_order_reference': return_order.reference,
                    'return_order_id': return_order.id
                }, status=status.HTTP_201_CREATED)
                
        except Exception as e:
            logger.error(f"Error creating return order for PO {purchase_order.reference}: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    # ==================== ANALYTICS AND REPORTING ====================
    
    @action(detail=False, methods=['get'])
    def analytics(self, request):
        """Get comprehensive purchase order analytics"""
        queryset = self.get_queryset()
        
        # Basic metrics
        total_orders = queryset.count()
        
        # Status distribution
        status_counts = queryset.aggregate(
            pending=Count(Case(When(status=PurchaseOrderStatus.PENDING, then=1))),
            approved=Count(Case(When(status=PurchaseOrderStatus.APPROVED, then=1))),
            issued=Count(Case(When(status=PurchaseOrderStatus.ISSUED, then=1))),
            received=Count(Case(When(status=PurchaseOrderStatus.RECEIVED, then=1))),
            completed=Count(Case(When(status=PurchaseOrderStatus.COMPLETED, then=1))),
            cancelled=Count(Case(When(status=PurchaseOrderStatus.CANCELLED, then=1)))
        )
        
        # Financial metrics
        # Annotate each PO with its calculated total price from its line items
        annotated_queryset = queryset.annotate(
            calculated_total=Sum(
                F('line_items__quantity') * F('line_items__unit_price'),
                output_field=DecimalField()
            )
        )

        # Aggregate the annotated values
        financial_metrics = annotated_queryset.aggregate(
            total_value=Sum('calculated_total', default=Decimal('0')),
            average_value=Avg('calculated_total', default=Decimal('0'))
        )

        total_value = financial_metrics['total_value'] or Decimal('0')
        average_value = financial_metrics['average_value'] or Decimal('0')
        
        # Time-based analytics
        monthly_trends = self._get_monthly_trends(queryset)
        weekly_trends = self._get_weekly_trends(queryset)
        
        # Supplier analytics
        supplier_performance = self._get_supplier_performance(queryset,)
        top_suppliers = self._get_top_suppliers_by_value(queryset)
        
        # Performance metrics
        performance_metrics = self._get_performance_metrics(queryset)
        
        analytics_data = {
            'total_purchase_orders': total_orders,
            'pending_orders': status_counts['pending'],
            'approved_orders': status_counts['approved'],
            'issued_orders': status_counts['issued'],
            'received_orders': status_counts['received'],
            'completed_orders': status_counts['completed'],
            'cancelled_orders': status_counts['cancelled'],
            
            'total_order_value': total_value,
            'average_order_value': average_value,
            
            'monthly_trends': monthly_trends,
            'weekly_trends': weekly_trends,
            
            'supplier_performance': supplier_performance,
            'top_suppliers_by_value': top_suppliers,
            
            'status_distribution': status_counts,
            
            **performance_metrics
        }
        
        serializer = PurchaseOrderAnalyticsSerializer(analytics_data)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def dashboard_summary(self, request):
        """Get dashboard summary for purchase orders"""
        queryset = self.get_queryset()
        
        # Quick metrics for dashboard
        today = timezone.now().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)
        
        summary = {
            'total_orders': queryset.count(),
            'active_orders': queryset.exclude(status__in=[PurchaseOrderStatus.COMPLETED, PurchaseOrderStatus.CANCELLED]).count(),
            'pending_approval': queryset.filter(status=PurchaseOrderStatus.PENDING).count(),
            'issued_orders': queryset.filter(status=PurchaseOrderStatus.ISSUED).count(),
            'received_orders': queryset.filter(status=PurchaseOrderStatus.RECEIVED).count(),
            'partially_received_orders': queryset.filter(workflow_state='PARTIALLY_RECEIVED').count(),
            'awaiting_receipt_orders': queryset.filter(
                Q(status=PurchaseOrderStatus.ISSUED) | Q(workflow_state='PARTIALLY_RECEIVED')
            ).distinct().count(),
            'ready_to_close_orders': queryset.filter(status=PurchaseOrderStatus.RECEIVED).count(),
            'overdue_orders': queryset.filter(
                delivery_date__lt=today,
                status__in=[PurchaseOrderStatus.ISSUED, PurchaseOrderStatus.APPROVED]
            ).count(),
            'orders_this_week': queryset.filter(created_at__gte=week_ago).count(),
            'orders_this_month': queryset.filter(created_at__gte=month_ago).count(),
            'total_value_this_month': sum(obj.total_price for obj in queryset.filter(created_at__gte=month_ago))
        }
        
        return Response(summary)
    
    # ==================== HELPER METHODS ====================
    
    def _get_monthly_trends(self, queryset):
        """Get monthly trends for the last 12 months"""
        trends = []
        for i in range(12):
            month_start = timezone.now().replace(day=1) - timedelta(days=30*i)
            month_end = month_start + timedelta(days=30)
            
            # month_data = queryset.filter(
            #     created_at__gte=month_start,
            #     created_at__lt=month_end
            # ).aggregate(
            #     count=Count('id'),
            #     total_value=Sum('total_price')
            # )
            
            qs = queryset.filter(
                created_at__gte=month_start,
                created_at__lt=month_end
            )
            month_data = {
                "count": qs.count(),
                "total_value": sum(obj.total_price for obj in qs)
            }
            trends.append({
                'month': month_start.strftime('%Y-%m'),
                'count': month_data['count'],
                'total_value': month_data.get('total_value', 0)
            })
        
        return trends
    
    def _get_weekly_trends(self, queryset):
        """Get weekly trends for the last 8 weeks"""
        trends = []
        for i in range(8):
            week_start = timezone.now().date() - timedelta(days=7*i)
            week_end = week_start + timedelta(days=7)
            
            qs = queryset.filter(
                created_at__date__gte=week_start,
                created_at__date__lt=week_end
            )
            
            trends.append({
                'week': week_start.strftime('%Y-W%U'),
                'count': qs.count(),
                'total_value': sum(obj.total_price for obj in qs)
            })
        
        return trends
    
    def _get_supplier_performance(self, queryset, month_start=None, month_end=None):
        """Get supplier performance metrics (Python-side aggregation)."""
        if month_start and month_end:
            qs = queryset.filter(
                created_at__gte=month_start,
                created_at__lt=month_end
            ).prefetch_related("line_items", "supplier")
        else:
            qs = queryset.prefetch_related("line_items", "supplier")
        data = {}

        for order in qs:
            supplier = order.supplier.name

            if supplier not in data:
                data[supplier] = {
                    "supplier__name": supplier,
                    "order_count": 0,
                    "total_value": Decimal("0"),
                    "avg_delivery_time": [],
                    "on_time_deliveries": 0,
                }

            d = data[supplier]

            # count orders
            d["order_count"] += 1

            # sum using your @property total_price
            d["total_value"] += order.total_price

            # delivery time calculation
            if order.delivery_date and order.issue_date:
                issue_date = order.issue_date.date() if hasattr(order.issue_date, "date") else order.issue_date
                d["avg_delivery_time"].append(order.delivery_date - issue_date)

            # on-time deliveries
            if order.received_date and order.delivery_date and order.received_date <= order.delivery_date:
                d["on_time_deliveries"] += 1

        # finalize averages
        for supplier, d in data.items():
            if d["avg_delivery_time"]:
                d["avg_delivery_time"] = sum(d["avg_delivery_time"], timedelta(0)) / len(d["avg_delivery_time"])
            else:
                d["avg_delivery_time"] = timedelta(0)

        # convert dict to list and sort
        supplier_performance = sorted(
            data.values(),
            key=lambda x: x["total_value"],
            reverse=True
        )[:10]

        return supplier_performance


    def _get_top_suppliers_by_value(self, queryset, month_start=None, month_end=None):
        """Get top suppliers by total order value (Python-side aggregation)."""

        qs = queryset
        if month_start and month_end:
            qs = qs.filter(created_at__gte=month_start, created_at__lt=month_end)

        qs = qs.prefetch_related("line_items", "supplier")

        data = {}

        for order in qs:
            supplier_id = order.supplier.id
            supplier_name = order.supplier.name

            if supplier_id not in data:
                data[supplier_id] = {
                    "supplier__id": supplier_id,
                    "supplier__name": supplier_name,
                    "order_count": 0,
                    "total_value": Decimal("0"),
                }

            d = data[supplier_id]

            # count orders
            d["order_count"] += 1

            # add total value using your @property
            d["total_value"] += order.total_price

        # convert dict to list and sort by total_value
        top_suppliers = sorted(
            data.values(),
            key=lambda x: x["total_value"],
            reverse=True
        )[:5]

        return top_suppliers

    def _get_performance_metrics(self, queryset):
        """Calculate performance metrics (Python-side for total_price)."""

        completed_orders = queryset.filter(status=PurchaseOrderStatus.COMPLETED).prefetch_related("line_items")

        # Average processing time (from creation to completion)
        processing_deltas = [
            (o.complete_date - o.created_at)
            for o in completed_orders
            if o.complete_date and o.created_at
        ]
        avg_processing_time = sum(processing_deltas, timedelta(0)) / len(processing_deltas) if processing_deltas else timedelta(0)

        # Average delivery time (from issue to delivery)
        delivery_deltas = [
            (
                o.received_date
                - (o.issue_date.date() if hasattr(o.issue_date, "date") else o.issue_date)
            )
            for o in completed_orders
            if o.received_date and o.issue_date
        ]
        avg_delivery_time = sum(delivery_deltas, timedelta(0)) / len(delivery_deltas) if delivery_deltas else timedelta(0)

        # On-time delivery rate
        total_delivered = sum(1 for o in completed_orders if o.received_date and o.delivery_date)
        on_time_delivered = sum(1 for o in completed_orders if o.received_date and o.delivery_date and o.received_date <= o.delivery_date)
        on_time_rate = (on_time_delivered / total_delivered * 100) if total_delivered > 0 else 0

        # Financial metrics (Python side using your @property)
        all_orders = queryset.prefetch_related("line_items")
        total_value = sum(o.total_price for o in all_orders)
        order_count = all_orders.count()

        avg_cost_per_order = (total_value / order_count) if order_count > 0 else Decimal("0.00")

        total_savings = Decimal("0.00")  # still placeholder until you define business logic

        return {
            "average_processing_time": avg_processing_time.days if avg_processing_time else 0,
            "average_delivery_time": avg_delivery_time.days if avg_delivery_time else 0,
            "on_time_delivery_rate": round(on_time_rate, 2),
            "total_savings": total_savings,
            "cost_per_order": avg_cost_per_order,
        }

    def _log_activity(self, action, instance, details):
        """Log user activity for audit trail"""
        try:
            current_user_id = get_request_user_id(self.request, as_str=False)
            if current_user_id:

                logger.info(
                    f"User {current_user_id} performed {action} "
                    f"on PO {instance.reference}: {details}"
                )
        except Exception as e:
            logger.error(f"Failed to log activity: {str(e)}")
    
    def _get_field_changes(self, original_data, new_data):
        """Compare original and new data to track changes"""
        changes = {}
        for key, new_value in new_data.items():
            old_value = original_data.get(key)
            if old_value != new_value:
                changes[key] = {
                    'old': old_value,
                    'new': new_value
                }
        return changes
    
    def _workspace_owner_recipient(self, purchase_order):
        """Return the workspace owner as an internal notification recipient."""
        try:
            profile = (
                IdentityCompanyProfile.objects.filter(
                    profile_id=purchase_order.profile_id,
                    is_active=True,
                )
                .only("owner_user_id")
                .first()
            )
            owner_user_id = str(getattr(profile, "owner_user_id", "") or "").strip()
            owner = IdentityDirectory.get_user_details(owner_user_id)
        except Exception:
            logger.exception("Could not resolve the workspace owner for purchase order delivery")
            return None

        owner_email = str((owner or {}).get("email") or "").strip()
        if not owner_user_id or not owner_email:
            return None
        return {
            "kind": "user",
            "user_id": owner_user_id,
            "email_snapshot": owner_email,
            "display_name": str((owner or {}).get("full_name") or "Workspace owner").strip(),
        }

    def _publish_purchase_order_email_dispatch(self, purchase_order, *, delivery_key: str, email_required: bool):
        contact = purchase_order.contact
        document_url = build_purchase_order_pdf_url(purchase_order)
        recipients_by_email = {}
        contact_email = str(getattr(contact, "email", "") or "").strip()
        if contact_email:
            recipients_by_email[contact_email.lower()] = {
                "kind": "external_email",
                "email": contact_email,
                "display_name": getattr(contact, "name", "") or "Supplier",
            }
        supplier_email = str(getattr(purchase_order.supplier, "email", "") or "").strip()
        if supplier_email:
            recipients_by_email.setdefault(
                supplier_email.lower(),
                {
                    "kind": "external_email",
                    "email": supplier_email,
                    "display_name": getattr(purchase_order.supplier, "name", "") or "Supplier",
                },
            )
        owner_recipient = self._workspace_owner_recipient(purchase_order)
        if owner_recipient:
            # Use the internal recipient when the owner and supplier share an email.
            # This prevents duplicate email jobs while retaining the owner's in-app copy.
            recipients_by_email[owner_recipient["email_snapshot"].lower()] = owner_recipient
        if not recipients_by_email:
            raise ValueError(f"Purchase order {purchase_order.reference} has no email recipient.")
        line_items = []
        line_item_manager = getattr(purchase_order, "line_items", None)
        if line_item_manager is not None:
            for line_item in line_item_manager.select_related("inventory_item").all():
                line_items.append(
                    {
                        "name": str(line_item.inventory_item),
                        "description": str(line_item.description or "").strip(),
                        "quantity": str(line_item.quantity),
                        "unit_price": f"{Decimal(str(line_item.unit_price)):,.2f}",
                        "line_total": f"{Decimal(str(line_item.total_price)):,.2f}",
                    }
                )
        publish_notification_dispatch(
            key=str(purchase_order.profile_id),
            payload={
                "notification_type": "purchase_order.issued.v1",
                "workspace_id": str(purchase_order.profile_id),
                "resource": {"type": "purchase_order", "id": str(purchase_order.id), "reference": purchase_order.reference},
                "recipients": list(recipients_by_email.values()),
                "channels": {
                    "email": "required" if email_required else "disabled",
                    "in_app": "disabled",
                    "realtime": "disabled",
                },
                "template": {
                    "key": "purchase_order_issued",
                    "version": 1,
                    "data": {
                        "purchase_order_reference": purchase_order.reference,
                        "supplier_name": purchase_order.supplier.name if purchase_order.supplier else "Supplier",
                        "issued_by_name": get_workspace_display_name(purchase_order),
                        "delivery_date": purchase_order.delivery_date.isoformat() if purchase_order.delivery_date else "",
                        "document_url": document_url,
                        "order_currency": str(getattr(purchase_order, "order_currency", "") or "").strip(),
                        "line_items": line_items,
                    },
                },
                "action": {"url": document_url, "label": "Open purchase order"},
                "attachments": [{"filename": f"PurchaseOrder_{purchase_order.reference}.pdf", "content_type": "application/pdf", "download_url": document_url}],
                "email_thread": {
                    "key": f"purchase-order:{purchase_order.id}",
                    "is_reply": ":resend:" in delivery_key,
                },
                "idempotency_key": delivery_key,
                "correlation_id": str(purchase_order.id),
            },
        )

    def _send_purchase_order_email(self, purchase_order, *, delivery_key: str | None = None):
        """Use a feature-gated direct/shadow/notification-service PO delivery path."""
        delivery_mode = settings.PURCHASE_ORDER_EMAIL_DELIVERY_MODE
        if delivery_mode not in {"direct", "shadow", "notification_service"}:
            raise ValueError("PURCHASE_ORDER_EMAIL_DELIVERY_MODE must be direct, shadow, or notification_service.")
        if delivery_mode in {"shadow", "notification_service"}:
            self._publish_purchase_order_email_dispatch(
                purchase_order,
                delivery_key=delivery_key or f"purchase-order:{purchase_order.id}:issued",
                email_required=delivery_mode == "notification_service",
            )
        if delivery_mode in {"shadow", "notification_service"}:
            logger.info(
                "%s purchase order email dispatch for %s",
                "Queued" if delivery_mode == "notification_service" else "Recorded shadow",
                purchase_order.reference,
            )
            return
        try:
            # Generate PDF
            pdf_content = PDFService.generate_purchase_order_pdf(purchase_order)
            
            # Send email
            success = EmailService.send_purchase_order_email(
                purchase_order=purchase_order,
                pdf_file=pdf_content
            )
            
            if not success:
                raise Exception("Failed to send purchase order email")
                
            logger.info(f"Successfully sent purchase order email for {purchase_order.reference}")
            
        except Exception as e:
            logger.error(f"Failed to send purchase order email: {str(e)}")
            raise

    def _publish_return_order_email_dispatch(self, return_order, purchase_order, *, delivery_key: str, email_required: bool):
        supplier = purchase_order.supplier
        recipients_by_email = {}
        supplier_email = str(getattr(supplier, "email", "") or "").strip()
        if supplier_email:
            recipients_by_email[supplier_email.lower()] = {
                "kind": "external_email",
                "email": supplier_email,
                "display_name": getattr(supplier, "name", "") or "Supplier",
            }
        contact = return_order.contact
        contact_email = str(getattr(contact, "email", "") or "").strip()
        if contact_email:
            recipients_by_email[contact_email.lower()] = {
                "kind": "external_email",
                "email": contact_email,
                "display_name": getattr(contact, "name", "") or "Supplier",
            }
        if not recipients_by_email:
            raise ValueError(f"Return order {return_order.reference} has no supplier email recipient.")

        purchase_order_url = build_purchase_order_pdf_url(purchase_order)
        return_order_url = build_return_order_pdf_url(return_order)
        publish_notification_dispatch(
            key=str(return_order.profile_id),
            payload={
                "notification_type": "return_order.created.v1",
                "workspace_id": str(return_order.profile_id),
                "resource": {"type": "return_order", "id": str(return_order.id), "reference": return_order.reference},
                "recipients": list(recipients_by_email.values()),
                "channels": {
                    "email": "required" if email_required else "disabled",
                    "in_app": "disabled",
                    "realtime": "disabled",
                },
                "template": {
                    "key": "return_order_created",
                    "version": 1,
                    "data": {
                        "purchase_order_reference": purchase_order.reference,
                        "return_order_reference": return_order.reference,
                        "supplier_name": getattr(supplier, "name", "") or "Supplier",
                        "company_name": get_workspace_display_name(return_order),
                        "return_document_url": return_order_url,
                        "purchase_order_document_url": purchase_order_url,
                    },
                },
                "action": {"url": return_order_url, "label": "Open return order"},
                "attachments": [
                    {"filename": f"Return_{return_order.reference}.pdf", "content_type": "application/pdf", "download_url": return_order_url},
                    {"filename": f"Original_PO_{purchase_order.reference}.pdf", "content_type": "application/pdf", "download_url": purchase_order_url},
                ],
                "email_thread": {"key": f"purchase-order:{purchase_order.id}", "is_reply": True},
                "idempotency_key": delivery_key,
                "correlation_id": str(return_order.id),
            },
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="notification-pdf",
        url_name="notification-pdf",
        authentication_classes=[],
        permission_classes=[AllowAny],
    )
    def notification_pdf(self, request, pk=None):
        """Serve one signed, short-lived PO PDF to the notification delivery service."""
        token = str(request.query_params.get("token") or "")
        try:
            verify_purchase_order_pdf_token(purchase_order_id=str(pk), token=token)
            purchase_order = self.get_queryset().get(pk=pk)
            pdf_content = PDFService.generate_purchase_order_pdf(purchase_order)
        except NotificationDocumentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except PurchaseOrder.DoesNotExist:
            return Response({"detail": "Purchase order not found."}, status=status.HTTP_404_NOT_FOUND)
        except PDFServiceUnavailableError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        response = HttpResponse(pdf_content.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="PurchaseOrder_{purchase_order.reference}.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response
    
    def _send_return_order_email(self, return_order, purchase_order, *, delivery_key: str | None = None):
        """Use a feature-gated direct/shadow/notification-service return-order delivery path."""
        delivery_mode = settings.RETURN_ORDER_EMAIL_DELIVERY_MODE
        if delivery_mode not in {"direct", "shadow", "notification_service"}:
            raise ValueError("RETURN_ORDER_EMAIL_DELIVERY_MODE must be direct, shadow, or notification_service.")
        if delivery_mode in {"shadow", "notification_service"}:
            self._publish_return_order_email_dispatch(
                return_order,
                purchase_order,
                delivery_key=delivery_key or f"return-order:{return_order.id}:created",
                email_required=delivery_mode == "notification_service",
            )
        if delivery_mode in {"shadow", "notification_service"}:
            logger.info(
                "%s return order email dispatch for %s",
                "Queued" if delivery_mode == "notification_service" else "Recorded shadow",
                return_order.reference,
            )
            return
        try:
            # Generate PDFs
            po_pdf = PDFService.generate_purchase_order_pdf(purchase_order)
            return_pdf = PDFService.generate_return_order_pdf(return_order)
            
            # Send email
            success = EmailService.send_return_order_email(
                return_order=return_order,
                po_pdf=po_pdf,
                return_pdf=return_pdf
            )
            
            if not success:
                raise Exception("Failed to send return order email")
                
            logger.info(f"Successfully sent return order email for {return_order.reference}")
            
        except Exception as e:
            logger.error(f"Failed to send return order email: {str(e)}")
            raise
    
    # Add new PDF generation endpoints
    @action(detail=True, methods=['get'])
    def download_pdf(self, request, pk=None):
        """Download purchase order as PDF"""
        purchase_order = self.get_object()
        
        try:
            pdf_content = PDFService.generate_purchase_order_pdf(purchase_order)
            payload = serialize_purchase_order(purchase_order)
            publish_order_admin_event(
                event_name='purchase_order.pdf_downloaded',
                payload=payload,
                actor=_audit_actor_from_request(request),
                target={
                    'type': 'purchase_order',
                    'id': str(purchase_order.id),
                    'label': purchase_order.reference,
                },
                summary=f'Purchase order PDF downloaded: {purchase_order.reference}.',
                metadata={'format': 'pdf'},
                after=payload,
                feature_area='purchasing_documents',
                reference_number=purchase_order.reference,
            )
            
            response = HttpResponse(pdf_content.getvalue(), content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="PO_{purchase_order.reference}.pdf"'
            
            return response
            
        except PDFServiceUnavailableError as e:
            logger.warning(f"PDF service unavailable for PO {purchase_order.reference}: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except Exception as e:
            logger.error(
                "Failed to generate PDF for PO %s: %s",
                purchase_order.reference,
                str(e),
                exc_info=True,
            )
            return Response(
                {'error': 'Failed to generate PDF'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def bulk_pdf_download(self, request):
        """Generate PDF for multiple purchase orders"""
        order_ids = request.data.get('order_ids', [])
        
        if not order_ids:
            return Response(
                {'error': 'order_ids list is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            purchase_orders = self.get_queryset().filter(id__in=order_ids)
            
            if not purchase_orders.exists():
                return Response(
                    {'error': 'No valid purchase orders found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Generate summary PDF
            pdf_content = PDFService.generate_purchase_order_summary_pdf(
                purchase_orders,
                date_range=request.data.get('date_range')
            )
            profile_id = get_request_profile_id(request, as_str=False)
            payload = {
                'profile_id': profile_id,
                'purchase_order_ids': [str(order.id) for order in purchase_orders],
                'purchase_order_references': [order.reference for order in purchase_orders],
                'count': purchase_orders.count(),
            }
            publish_order_admin_event(
                event_name='purchase_order.bulk_pdf_downloaded',
                payload=payload,
                actor=_audit_actor_from_request(request),
                target={
                    'type': 'purchase_order_bulk_export',
                    'id': timezone.now().strftime('%Y%m%d%H%M%S'),
                    'label': f'{purchase_orders.count()} purchase orders',
                },
                summary='Bulk purchase order PDF download generated.',
                metadata={'format': 'pdf', 'date_range': request.data.get('date_range')},
                after=payload,
                feature_area='purchasing_documents',
            )
            
            response = HttpResponse(pdf_content.getvalue(), content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="PO_Summary_{timezone.now().strftime("%Y%m%d")}.pdf"'
            
            return response
            
        except PDFServiceUnavailableError as e:
            logger.warning(f"PDF service unavailable for bulk PDF generation: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except Exception as e:
            logger.error(f"Failed to generate bulk PDF: {str(e)}")
            return Response(
                {'error': 'Failed to generate PDF'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def resend_email(self, request):
        """Resend purchase order email"""
        order_id = request.data.get('order_id')
        
        if not order_id:
            return Response(
                {'error': 'order_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            purchase_order = self.get_queryset().get(id=order_id)
            
            # Check if order is in a state where email can be sent
            if purchase_order.status not in [PurchaseOrderStatus.ISSUED, PurchaseOrderStatus.APPROVED]:
                return Response(
                    {'error': 'Can only resend emails for issued or approved orders'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            self._send_purchase_order_email(
                purchase_order,
                delivery_key=f"purchase-order:{purchase_order.id}:resend:{timezone.now().isoformat()}",
            )
            payload = serialize_purchase_order(purchase_order)
            publish_order_admin_event(
                event_name='purchase_order.email_resent',
                payload=payload,
                actor=_audit_actor_from_request(request),
                target={
                    'type': 'purchase_order',
                    'id': str(purchase_order.id),
                    'label': purchase_order.reference,
                },
                summary=f'Purchase order email resent: {purchase_order.reference}.',
                metadata={'status': purchase_order.status},
                after=payload,
                feature_area='purchasing_notifications',
                reference_number=purchase_order.reference,
            )
            
            # Log activity
            current_user = IdentityDirectory.get_current_user(request)
            current_user_id = get_request_user_id(request, as_str=False)
            self._log_activity('RESEND_EMAIL', purchase_order, {
                'resent_by': current_user.get('full_name') if current_user_id else 'Unknown'
            })
            
            return Response({
                'message': f'Email resent successfully for PO {purchase_order.reference}'
            })
            
        except PurchaseOrder.DoesNotExist:
            return Response(
                {'error': 'Purchase order not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except PDFServiceUnavailableError as e:
            logger.warning(f"PDF service unavailable while resending PO email: {str(e)}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except Exception as e:
            logger.error(f"Failed to resend email: {str(e)}")
            return Response(
                {'error': f'Failed to resend email: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SalesOrderViewSet(BaseCachePermissionViewset):
    required_permission = UNIFIED_PERMISSION_DICT.get('sales_order')
    queryset = SalesOrder.objects.select_related(
        'customer',
        'contact',
        'address',
    ).prefetch_related(
        'line_items',
        'line_items__inventory_item',
        'shipments',
        'shipments__lines',
        'shipments__lines__stock_location',
        'shipments__lines__stock_lot',
        'shipments__lines__stock_serial',
        'shipments__lines__reservation',
    )
    filterset_fields = ['status', 'customer', 'issue_date', 'shipment_date', 'delivery_date']
    search_fields = ['reference', 'description', 'customer_reference', 'customer__name']
    ordering_fields = ['reference', 'issue_date', 'delivery_date', 'created_at']
    ordering = ['-created_at']

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

        status_filter = (self.request.query_params.get('status_filter') or '').strip().lower()
        if status_filter == 'active':
            queryset = queryset.exclude(status__in=[SalesOrderStatus.COMPLETED, SalesOrderStatus.CANCELLED])
        elif status_filter == 'ready_to_close':
            queryset = queryset.filter(status=SalesOrderStatus.SHIPPED)

        date_from = self.request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)

        date_to = self.request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return SalesOrderListSerializer
        if self.action == 'reserve':
            return SalesOrderReserveSerializer
        if self.action == 'release':
            return SalesOrderReleaseSerializer
        if self.action == 'ship':
            return SalesOrderShipSerializer
        return SalesOrderDetailSerializer

    @action(detail=False, methods=['get'])
    def summary(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        return Response(
            {
                'total_orders': queryset.count(),
                'active_orders': queryset.exclude(status__in=[SalesOrderStatus.COMPLETED, SalesOrderStatus.CANCELLED]).count(),
                'pending_orders': queryset.filter(status=SalesOrderStatus.PENDING).count(),
                'in_progress_orders': queryset.filter(status=SalesOrderStatus.IN_PROGRESS).count(),
                'shipped_orders': queryset.filter(status=SalesOrderStatus.SHIPPED).count(),
                'ready_to_close_orders': queryset.filter(status=SalesOrderStatus.SHIPPED).count(),
            }
        )

    def perform_create(self, serializer):
        current_user_id = get_request_user_id(self.request, as_str=False)
        profile_id = get_request_profile_id(self.request, required=True, as_str=False)
        extra_fields = {
            'status': SalesOrderStatus.PENDING,
            'profile_id': profile_id,
        }
        if current_user_id:
            extra_fields['created_by_user_id'] = current_user_id
            extra_fields['responsible_user_id'] = current_user_id
        instance = serializer.save(**extra_fields)
        payload = serialize_sales_order(instance)
        publish_order_admin_event(
            event_name='sales_order.created',
            payload=payload,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'sales_order',
                'id': str(instance.id),
                'label': instance.reference,
            },
            summary=f'Sales order created: {instance.reference}.',
            metadata={
                'status': instance.status,
                'customer_id': str(instance.customer_id or ''),
                'customer_name': getattr(instance.customer, 'name', '') or '',
            },
            after=payload,
            feature_area='sales_orders',
            reference_number=instance.reference,
            notification_category='sales_order',
            notification_title=f'Sales order {instance.reference} created',
            notification_message=(
                f"Sales order {instance.reference} was created"
                f"{f' for {getattr(instance.customer, 'name', '')}' if getattr(instance.customer, 'name', '') else ''}."
            ),
            notification_action_url='/order/sales',
        )
        self._log_activity('CREATE', instance, {'initial_data': self.request.data})
        self._invalidate_cache()

    def perform_update(self, serializer):
        current_user_id = get_request_user_id(self.request, as_str=False)
        before = serialize_sales_order(serializer.instance)
        extra_fields = {}
        if current_user_id:
            extra_fields['updated_by_user_id'] = current_user_id
        instance = serializer.save(**extra_fields)
        after = serialize_sales_order(instance)
        publish_order_admin_event(
            event_name='sales_order.updated',
            payload=after,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'sales_order',
                'id': str(instance.id),
                'label': instance.reference,
            },
            summary=f'Sales order updated: {instance.reference}.',
            metadata={
                'status': instance.status,
                'updated_fields': list(serializer.validated_data.keys()),
            },
            before=before,
            after=after,
            feature_area='sales_orders',
            reference_number=instance.reference,
        )
        self._log_activity('UPDATE', instance, {'updated_fields': list(serializer.validated_data.keys())})
        self._invalidate_cache()

    def update(self, request, *args, **kwargs):
        sales_order = self.get_object()
        lock_reason = _sales_order_edit_lock_reason(sales_order)
        if lock_reason:
            return Response({'error': lock_reason}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        sales_order = self.get_object()
        lock_reason = _sales_order_edit_lock_reason(sales_order)
        if lock_reason:
            return Response({'error': lock_reason}, status=status.HTTP_400_BAD_REQUEST)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        sales_order = self.get_object()
        lock_reason = _sales_order_edit_lock_reason(sales_order)
        if lock_reason:
            return Response({'error': lock_reason}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        before = serialize_sales_order(instance)
        publish_order_admin_event(
            event_name='sales_order.deleted',
            payload=before,
            actor=_audit_actor_from_request(self.request),
            target={
                'type': 'sales_order',
                'id': str(instance.id),
                'label': instance.reference,
            },
            summary=f'Sales order deleted: {instance.reference}.',
            metadata={'status': instance.status},
            before=before,
            after={},
            severity='warning',
            feature_area='sales_orders',
            reference_number=instance.reference,
        )
        instance.delete()
        self._invalidate_cache()

    @action(detail=True, methods=['get'])
    def line_items(self, request, pk=None):
        sales_order = self.get_object()
        serializer = SalesOrderLineItemSerializer(sales_order.line_items.all(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def shipments(self, request, pk=None):
        sales_order = self.get_object()
        serializer = SalesOrderShipmentSerializer(sales_order.shipments.all(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def add_line_item(self, request, pk=None):
        sales_order = self.get_object()
        if sales_order.status not in [SalesOrderStatus.PENDING, SalesOrderStatus.IN_PROGRESS]:
            return Response(
                {'error': 'Cannot add line items to this sales order in its current status'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = SalesOrderLineItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        line_item = serializer.save(sales_order=sales_order)
        payload = serialize_sales_order_line_item(line_item)
        publish_order_admin_event(
            event_name='sales_order.line_item.added',
            payload=payload,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'sales_order_line_item',
                'id': str(line_item.id),
                'label': f'{sales_order.reference} / {line_item.inventory_item.name_snapshot}',
                'barcode': line_item.inventory_item.barcode_snapshot or '',
                'sku': line_item.inventory_item.sku_snapshot or '',
            },
            summary=f'Line item added to sales order {sales_order.reference}.',
            metadata={
                'sales_order_id': str(sales_order.id),
                'sales_order_reference': sales_order.reference,
            },
            after=payload,
            feature_area='sales_orders',
            reference_number=sales_order.reference,
        )
        self._log_activity('ADD_LINE_ITEM', sales_order, {'line_item_id': str(line_item.id)})
        self._invalidate_cache()
        return Response(SalesOrderLineItemSerializer(line_item).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['put', 'patch'])
    def update_line_item(self, request, pk=None):
        sales_order = self.get_object()
        if sales_order.status not in [SalesOrderStatus.PENDING, SalesOrderStatus.IN_PROGRESS]:
            return Response(
                {'error': 'Cannot update line items for this sales order in its current status'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        line_item_id = request.data.get('line_item_id')
        if not line_item_id:
            return Response({'error': 'line_item_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            line_item = sales_order.line_items.get(id=line_item_id)
        except SalesOrderLineItem.DoesNotExist:
            return Response({'error': 'Line item not found'}, status=status.HTTP_404_NOT_FOUND)

        serializer = SalesOrderLineItemCreateSerializer(line_item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = serialize_sales_order_line_item(line_item)
        line_item = serializer.save()
        after = serialize_sales_order_line_item(line_item)
        publish_order_admin_event(
            event_name='sales_order.line_item.updated',
            payload=after,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'sales_order_line_item',
                'id': str(line_item.id),
                'label': f'{sales_order.reference} / {line_item.inventory_item.name_snapshot}',
                'barcode': line_item.inventory_item.barcode_snapshot or '',
                'sku': line_item.inventory_item.sku_snapshot or '',
            },
            summary=f'Line item updated on sales order {sales_order.reference}.',
            metadata={
                'sales_order_id': str(sales_order.id),
                'sales_order_reference': sales_order.reference,
            },
            before=before,
            after=after,
            feature_area='sales_orders',
            reference_number=sales_order.reference,
        )
        self._log_activity('UPDATE_LINE_ITEM', sales_order, {'line_item_id': str(line_item.id)})
        self._invalidate_cache()
        return Response(SalesOrderLineItemSerializer(line_item).data)

    @action(detail=True, methods=['delete'])
    def remove_line_item(self, request, pk=None):
        sales_order = self.get_object()
        if sales_order.status not in [SalesOrderStatus.PENDING, SalesOrderStatus.IN_PROGRESS]:
            return Response(
                {'error': 'Cannot remove line items from this sales order in its current status'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        line_item_id = request.query_params.get('line_item_id')
        if not line_item_id:
            return Response({'error': 'line_item_id parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            line_item = sales_order.line_items.get(id=line_item_id)
        except SalesOrderLineItem.DoesNotExist:
            return Response({'error': 'Line item not found'}, status=status.HTTP_404_NOT_FOUND)

        if Decimal(str(line_item.shipped_quantity)) > 0 or Decimal(str(line_item.reserved_quantity)) > 0:
            return Response(
                {'error': 'Cannot remove a line item with reserved or shipped stock'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        before = serialize_sales_order_line_item(line_item)
        publish_order_admin_event(
            event_name='sales_order.line_item.removed',
            payload=before,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'sales_order_line_item',
                'id': str(line_item.id),
                'label': f'{sales_order.reference} / {line_item.inventory_item.name_snapshot}',
                'barcode': line_item.inventory_item.barcode_snapshot or '',
                'sku': line_item.inventory_item.sku_snapshot or '',
            },
            summary=f'Line item removed from sales order {sales_order.reference}.',
            metadata={
                'sales_order_id': str(sales_order.id),
                'sales_order_reference': sales_order.reference,
            },
            before=before,
            after={},
            severity='warning',
            feature_area='sales_orders',
            reference_number=sales_order.reference,
        )
        line_item.delete()
        self._log_activity('REMOVE_LINE_ITEM', sales_order, {'line_item_id': str(line_item_id)})
        self._invalidate_cache()
        return Response({'message': 'Line item removed successfully'})

    @action(detail=True, methods=['post'])
    def reserve(self, request, pk=None):
        sales_order = self.get_object()
        if sales_order.status in [SalesOrderStatus.CANCELLED, SalesOrderStatus.COMPLETED]:
            return Response(
                {'error': 'Cannot reserve stock for a cancelled or completed sales order'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        current_user_id = get_request_user_id(request, as_str=False)
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        structural_scope_location_id = _resolve_structural_scope_location_id(request, payload)

        try:
            with transaction.atomic():
                before = serialize_sales_order(sales_order)
                reservations = []
                reservation_summaries = []
                for item in payload['reservation_items']:
                    line_item = sales_order.line_items.select_related('inventory_item').get(id=item['line_item_id'])
                    default_reserve_quantity = (
                        Decimal('1')
                        if item.get('stock_serial_id') or item.get('serial_number')
                        else line_item.reservable_quantity
                    )
                    reserve_quantity = Decimal(str(item.get('quantity', default_reserve_quantity)))
                    if reserve_quantity <= 0:
                        raise ValueError("Reservation quantity must be greater than zero")
                    if reserve_quantity > line_item.reservable_quantity:
                        raise ValueError(
                            f"Cannot reserve {reserve_quantity}; only {line_item.reservable_quantity} remains reservable"
                        )

                    stock_location = scope_queryset_by_identity(
                        StockLocation.objects.filter(id=item['location_id']),
                        canonical_field='profile_id',
                        legacy_field='profile',
                        value=profile_id,
                    ).first()
                    if stock_location is None:
                        raise ValueError(f"Stock location {item['location_id']} not found")
                    _assert_location_within_scope(
                        profile_id=profile_id,
                        structural_scope_location_id=structural_scope_location_id,
                        stock_location=stock_location,
                        label=f"Reservation location {item['location_id']}",
                    )

                    stock_lot = None
                    stock_lot_id = item.get('stock_lot_id')
                    if stock_lot_id:
                        stock_lot = StockLot.objects.filter(profile_id=profile_id, id=stock_lot_id).first()
                        if stock_lot is None:
                            raise ValueError(f"Stock lot {stock_lot_id} not found")

                    stock_serial = None
                    stock_serial_id = item.get('stock_serial_id')
                    if stock_serial_id:
                        stock_serial = StockSerial.objects.filter(profile_id=profile_id, id=stock_serial_id).first()
                        if stock_serial is None:
                            raise ValueError(f"Stock serial {stock_serial_id} not found")

                    reservation_result = StockDomainService.reserve_stock(
                        inventory_item=line_item.inventory_item,
                        stock_location=stock_location,
                        quantity=reserve_quantity,
                        external_order_type='sales_order_line',
                        external_order_id=str(sales_order.id),
                        external_order_line_id=str(line_item.id),
                        actor_user_id=current_user_id,
                        stock_lot=stock_lot,
                        stock_serial=stock_serial,
                        serial_number=item.get('serial_number', ''),
                        expires_at=payload.get('expires_at'),
                        notes=item.get('notes') or payload.get('notes', '') or f"Reserved for sales order {sales_order.reference}",
                    )
                    line_item.reserved_quantity = Decimal(str(line_item.reserved_quantity)) + reserve_quantity
                    line_item.updated_by_user_id = current_user_id
                    line_item.save()
                    reservations.append(str(reservation_result['reservation'].id))
                    reservation_summaries.append({
                        'reservation_id': str(reservation_result['reservation'].id),
                        'sales_order_line_item_id': str(line_item.id),
                        'inventory_item_id': str(line_item.inventory_item_id),
                        'inventory_name': line_item.inventory_item.name_snapshot,
                        'inventory_barcode': line_item.inventory_item.barcode_snapshot or '',
                        'inventory_sku': line_item.inventory_item.sku_snapshot or '',
                        'quantity': str(reserve_quantity),
                        'stock_location_id': str(stock_location.id),
                        'stock_location_name': stock_location.name,
                    })

                if sales_order.status == SalesOrderStatus.PENDING:
                    sales_order.status = SalesOrderStatus.IN_PROGRESS
                    sales_order.updated_by_user_id = current_user_id
                    sales_order.save()
                after = serialize_sales_order(sales_order)
                publish_order_admin_event(
                    event_name='sales_order.stock_reserved',
                    payload=after,
                    actor=_audit_actor_from_request(request),
                    target={
                        'type': 'sales_order',
                        'id': str(sales_order.id),
                        'label': sales_order.reference,
                    },
                    summary=f'Stock reserved for sales order {sales_order.reference}.',
                    metadata={
                        'reservation_count': len(reservations),
                        'reservations': reservation_summaries,
                    },
                    before=before,
                    after=after,
                    feature_area='sales_fulfillment',
                    reference_number=sales_order.reference,
                )

                self._log_activity('RESERVE_STOCK', sales_order, {
                    'reservation_count': len(reservations),
                    'reservation_ids': reservations,
                })

            self._invalidate_cache()
            return Response({
                'message': 'Stock reserved successfully',
                'reservation_count': len(reservations),
                'reservation_ids': reservations,
                'status': sales_order.status,
            })
        except SalesOrderLineItem.DoesNotExist:
            return Response({'error': 'Sales order line item not found'}, status=status.HTTP_404_NOT_FOUND)
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error(f"Error reserving stock for sales order {sales_order.reference}: {str(exc)}")
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def release(self, request, pk=None):
        sales_order = self.get_object()
        if sales_order.status in [SalesOrderStatus.CANCELLED, SalesOrderStatus.COMPLETED]:
            return Response(
                {'error': 'Cannot release reservations for a cancelled or completed sales order'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        current_user_id = get_request_user_id(request, as_str=False)
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        structural_scope_location_id = _resolve_structural_scope_location_id(request, payload)

        try:
            with transaction.atomic():
                before = serialize_sales_order(sales_order)
                released_count = 0
                released_summaries = []
                for item in payload['reservation_items']:
                    reservation = StockReservation.objects.select_related(
                        'stock_location',
                        'stock_lot',
                        'inventory_item',
                    ).filter(
                        profile_id=profile_id,
                        id=item['reservation_id'],
                        external_order_type='sales_order_line',
                        external_order_id=str(sales_order.id),
                    ).first()
                    if reservation is None:
                        raise ValueError(f"Reservation {item['reservation_id']} not found")
                    _assert_reservation_within_scope(
                        profile_id=profile_id,
                        structural_scope_location_id=structural_scope_location_id,
                        reservation=reservation,
                        label=f"Reservation {item['reservation_id']}",
                    )

                    release_quantity = Decimal(str(item.get('quantity', reservation.remaining_quantity)))
                    if release_quantity <= 0:
                        raise ValueError("Release quantity must be greater than zero")

                    StockDomainService.release_reservation(
                        reservation=reservation,
                        quantity=release_quantity,
                        actor_user_id=current_user_id,
                        notes=item.get('notes') or payload.get('notes', '') or f"Released reservation for {sales_order.reference}",
                    )

                    line_item = sales_order.line_items.get(id=reservation.external_order_line_id)
                    line_item.reserved_quantity = max(
                        Decimal(str(line_item.reserved_quantity)) - release_quantity,
                        Decimal('0'),
                    )
                    line_item.updated_by_user_id = current_user_id
                    line_item.save()
                    released_count += 1
                    released_summaries.append({
                        'reservation_id': str(reservation.id),
                        'sales_order_line_item_id': str(line_item.id),
                        'inventory_item_id': str(line_item.inventory_item_id),
                        'inventory_name': line_item.inventory_item.name_snapshot,
                        'inventory_barcode': line_item.inventory_item.barcode_snapshot or '',
                        'inventory_sku': line_item.inventory_item.sku_snapshot or '',
                        'released_quantity': str(release_quantity),
                    })

                if (
                    sales_order.status == SalesOrderStatus.IN_PROGRESS
                    and not sales_order.line_items.filter(
                        Q(reserved_quantity__gt=0) | Q(shipped_quantity__gt=0)
                    ).exists()
                ):
                    sales_order.status = SalesOrderStatus.PENDING
                    sales_order.updated_by_user_id = current_user_id
                    sales_order.save()
                after = serialize_sales_order(sales_order)
                publish_order_admin_event(
                    event_name='sales_order.reservations_released',
                    payload=after,
                    actor=_audit_actor_from_request(request),
                    target={
                        'type': 'sales_order',
                        'id': str(sales_order.id),
                        'label': sales_order.reference,
                    },
                    summary=f'Reservations released for sales order {sales_order.reference}.',
                    metadata={
                        'released_count': released_count,
                        'reservations': released_summaries,
                    },
                    before=before,
                    after=after,
                    feature_area='sales_fulfillment',
                    reference_number=sales_order.reference,
                )

                self._log_activity('RELEASE_RESERVATION', sales_order, {'released_count': released_count})

            self._invalidate_cache()
            return Response({
                'message': 'Reservations released successfully',
                'released_count': released_count,
                'status': sales_order.status,
            })
        except SalesOrderLineItem.DoesNotExist:
            return Response({'error': 'Sales order line item not found'}, status=status.HTTP_404_NOT_FOUND)
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error(f"Error releasing reservations for sales order {sales_order.reference}: {str(exc)}")
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def ship(self, request, pk=None):
        sales_order = self.get_object()
        if sales_order.status in [SalesOrderStatus.CANCELLED, SalesOrderStatus.COMPLETED]:
            return Response(
                {'error': 'Cannot ship a cancelled or completed sales order'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        current_user_id = get_request_user_id(request, as_str=False)
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        structural_scope_location_id = _resolve_structural_scope_location_id(request, payload)

        try:
            with transaction.atomic():
                before = serialize_sales_order(sales_order)
                shipment_items_payload = payload['shipment_items']
                for item in shipment_items_payload:
                    if 'reservation_id' in item:
                        reservation = StockReservation.objects.filter(
                            profile_id=profile_id,
                            id=item['reservation_id'],
                            external_order_type='sales_order_line',
                            external_order_id=str(sales_order.id),
                        ).first()
                        if reservation is None:
                            raise ValueError(f"Reservation {item['reservation_id']} not found")
                        _assert_reservation_within_scope(
                            profile_id=profile_id,
                            structural_scope_location_id=structural_scope_location_id,
                            reservation=reservation,
                            label=f"Reservation {item['reservation_id']}",
                        )
                        line_item = sales_order.line_items.select_related('inventory_item').get(
                            id=reservation.external_order_line_id
                        )
                    else:
                        line_item = sales_order.line_items.select_related('inventory_item').get(id=item['line_item_id'])

                shipment = SalesOrderShipment.objects.create(
                    order=sales_order,
                    shipment_date=payload.get('shipment_date') or timezone.now().date(),
                    delivery_date=payload.get('delivery_date'),
                    tracking_number=payload.get('tracking_number', ''),
                    invoice_number=payload.get('invoice_number', ''),
                    link=payload.get('link', ''),
                    notes=payload.get('notes', ''),
                    checked_by_user_id=current_user_id,
                    created_by_user_id=current_user_id,
                    updated_by_user_id=current_user_id,
                )

                shipment_line_count = 0
                shipment_line_summaries = []
                for item in shipment_items_payload:
                    notes = item.get('notes') or payload.get('notes', '') or f"Shipment {shipment.reference}"
                    reservation = None
                    stock_lot = None
                    stock_serial = None

                    if 'reservation_id' in item:
                        reservation = StockReservation.objects.select_related(
                            'stock_location',
                            'stock_lot',
                            'stock_serial',
                            'inventory_item',
                        ).filter(
                            profile_id=profile_id,
                            id=item['reservation_id'],
                            external_order_type='sales_order_line',
                            external_order_id=str(sales_order.id),
                        ).first()
                        if reservation is None:
                            raise ValueError(f"Reservation {item['reservation_id']} not found")
                        _assert_reservation_within_scope(
                            profile_id=profile_id,
                            structural_scope_location_id=structural_scope_location_id,
                            reservation=reservation,
                            label=f"Reservation {item['reservation_id']}",
                        )

                        line_item = sales_order.line_items.select_related('inventory_item').get(
                            id=reservation.external_order_line_id
                        )
                        ship_quantity = Decimal(str(item.get('quantity', reservation.remaining_quantity)))
                        if ship_quantity <= 0:
                            raise ValueError("Shipment quantity must be greater than zero")
                        if ship_quantity > reservation.remaining_quantity:
                            raise ValueError(
                                f"Cannot ship {ship_quantity}; reservation only has {reservation.remaining_quantity} remaining"
                            )

                        StockDomainService.fulfill_reservation(
                            reservation=reservation,
                            quantity=ship_quantity,
                            actor_user_id=current_user_id,
                            notes=notes,
                        )
                        line_item.reserved_quantity = max(
                            Decimal(str(line_item.reserved_quantity)) - ship_quantity,
                            Decimal('0'),
                        )
                        stock_location = reservation.stock_location
                        stock_lot = reservation.stock_lot
                        stock_serial = reservation.stock_serial
                    else:
                        line_item = sales_order.line_items.select_related('inventory_item').get(id=item['line_item_id'])
                        if Decimal(str(line_item.reserved_quantity)) > 0:
                            raise ValueError(
                                f"Line item {line_item.id} still has reserved stock. Fulfill or release reservations before direct shipping."
                            )
                        default_ship_quantity = (
                            Decimal('1')
                            if item.get('stock_serial_id') or item.get('serial_number')
                            else line_item.remaining_quantity
                        )
                        ship_quantity = Decimal(str(item.get('quantity', default_ship_quantity)))
                        if ship_quantity <= 0:
                            raise ValueError("Shipment quantity must be greater than zero")
                        if ship_quantity > line_item.remaining_quantity:
                            raise ValueError(
                                f"Cannot ship {ship_quantity}; only {line_item.remaining_quantity} remains on the line item"
                            )

                        stock_location = scope_queryset_by_identity(
                            StockLocation.objects.filter(id=item['location_id']),
                            canonical_field='profile_id',
                            legacy_field='profile',
                            value=profile_id,
                        ).first()
                        if stock_location is None:
                            raise ValueError(f"Stock location {item['location_id']} not found")
                        _assert_location_within_scope(
                            profile_id=profile_id,
                            structural_scope_location_id=structural_scope_location_id,
                            stock_location=stock_location,
                            label=f"Shipment location {item['location_id']}",
                        )

                        stock_lot_id = item.get('stock_lot_id')
                        if stock_lot_id:
                            stock_lot = StockLot.objects.filter(profile_id=profile_id, id=stock_lot_id).first()
                            if stock_lot is None:
                                raise ValueError(f"Stock lot {stock_lot_id} not found")

                        stock_serial_id = item.get('stock_serial_id')
                        if stock_serial_id:
                            stock_serial = StockSerial.objects.filter(profile_id=profile_id, id=stock_serial_id).first()
                            if stock_serial is None:
                                raise ValueError(f"Stock serial {stock_serial_id} not found")

                        StockDomainService.issue_stock(
                            inventory_item=line_item.inventory_item,
                            stock_location=stock_location,
                            quantity=ship_quantity,
                            actor_user_id=current_user_id,
                            stock_lot=stock_lot,
                            stock_serial=stock_serial,
                            serial_number=item.get('serial_number', ''),
                            reference_type='sales_order_line',
                            reference_id=str(line_item.id),
                            notes=notes,
                            movement_type=StockMovementType.ISSUE,
                            tracking_type=TrackingType.SHIPPED,
                        )

                    line_item.shipped_quantity = Decimal(str(line_item.shipped_quantity)) + ship_quantity
                    line_item.updated_by_user_id = current_user_id
                    line_item.save()

                    shipment.lines.create(
                        sales_order_line=line_item,
                        stock_location=stock_location,
                        stock_lot=stock_lot,
                        stock_serial=stock_serial,
                        reservation=reservation,
                        quantity_shipped=ship_quantity,
                        notes=notes,
                        created_by_user_id=current_user_id,
                        updated_by_user_id=current_user_id,
                    )
                    shipment_line_count += 1
                    shipment_line_summaries.append({
                        'sales_order_line_item_id': str(line_item.id),
                        'inventory_item_id': str(line_item.inventory_item_id),
                        'inventory_name': line_item.inventory_item.name_snapshot,
                        'inventory_barcode': line_item.inventory_item.barcode_snapshot or '',
                        'inventory_sku': line_item.inventory_item.sku_snapshot or '',
                        'quantity_shipped': str(ship_quantity),
                        'stock_location_id': str(stock_location.id),
                        'stock_location_name': stock_location.name,
                        'reservation_id': str(reservation.id) if reservation else '',
                        'lot_number': getattr(stock_lot, 'lot_number', '') or '',
                        'serial_number': getattr(stock_serial, 'serial_number', '') or '',
                    })

                sales_order.shipment_date = payload.get('shipment_date') or timezone.now()
                if not sales_order.issue_date:
                    sales_order.issue_date = timezone.now()
                sales_order.shipped_by_user_id = current_user_id
                sales_order.status = (
                    SalesOrderStatus.SHIPPED
                    if not sales_order.line_items.filter(shipped_quantity__lt=F('quantity')).exists()
                    else SalesOrderStatus.IN_PROGRESS
                )
                sales_order.updated_by_user_id = current_user_id
                sales_order.save()
                after = serialize_sales_order(sales_order)
                shipment_payload = serialize_sales_order_shipment(shipment)
                publish_order_admin_event(
                    event_name='sales_order.shipment_created',
                    payload=shipment_payload,
                    actor=_audit_actor_from_request(request),
                    target={
                        'type': 'sales_order_shipment',
                        'id': str(shipment.id),
                        'label': shipment.reference,
                    },
                    summary=f'Shipment created for sales order {sales_order.reference}: {shipment.reference}.',
                    metadata={
                        'sales_order_id': str(sales_order.id),
                        'sales_order_reference': sales_order.reference,
                        'shipment_line_count': shipment_line_count,
                        'shipment_lines': shipment_line_summaries,
                    },
                    after=shipment_payload,
                    feature_area='sales_shipments',
                    reference_number=shipment.reference,
                    notification_category='sales_order',
                    notification_title=f'Shipment {shipment.reference} created',
                    notification_message=(
                        f'Shipment {shipment.reference} was created for sales order {sales_order.reference}.'
                    ),
                    notification_action_url='/order/sales',
                )
                publish_order_admin_event(
                    event_name='sales_order.shipped',
                    payload=after,
                    actor=_audit_actor_from_request(request),
                    target={
                        'type': 'sales_order',
                        'id': str(sales_order.id),
                        'label': sales_order.reference,
                    },
                    summary=f'Sales order shipped: {sales_order.reference}.',
                    metadata={
                        'shipment_id': str(shipment.id),
                        'shipment_reference': shipment.reference,
                        'shipment_line_count': shipment_line_count,
                    },
                    before=before,
                    after=after,
                    feature_area='sales_fulfillment',
                    reference_number=sales_order.reference,
                    notification_category='sales_order',
                    notification_title=f'Sales order {sales_order.reference} shipped',
                    notification_message=f'Sales order {sales_order.reference} was shipped.',
                    notification_action_url='/order/sales',
                )

                self._log_activity('SHIP', sales_order, {
                    'shipment_reference': shipment.reference,
                    'shipment_line_count': shipment_line_count,
                })

            shipment.refresh_from_db()
            self._invalidate_cache()
            return Response(SalesOrderShipmentSerializer(shipment).data, status=status.HTTP_201_CREATED)
        except SalesOrderLineItem.DoesNotExist:
            return Response({'error': 'Sales order line item not found'}, status=status.HTTP_404_NOT_FOUND)
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error(f"Error shipping sales order {sales_order.reference}: {str(exc)}")
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        sales_order = self.get_object()
        if sales_order.status == SalesOrderStatus.CANCELLED:
            return Response(
                {'error': 'Cannot complete a cancelled sales order'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sales_order.line_items.filter(shipped_quantity__lt=F('quantity')).exists():
            return Response(
                {'error': 'All sales order line items must be fully shipped before completion'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        before = serialize_sales_order(sales_order)
        sales_order.status = SalesOrderStatus.COMPLETED
        sales_order.complete_date = timezone.now()
        sales_order.updated_by_user_id = get_request_user_id(request, as_str=False)
        sales_order.save()
        after = serialize_sales_order(sales_order)
        publish_order_admin_event(
            event_name='sales_order.completed',
            payload=after,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'sales_order',
                'id': str(sales_order.id),
                'label': sales_order.reference,
            },
            summary=f'Sales order completed: {sales_order.reference}.',
            metadata={'status': sales_order.status},
            before=before,
            after=after,
            feature_area='sales_orders',
            reference_number=sales_order.reference,
            notification_category='sales_order',
            notification_title=f'Sales order {sales_order.reference} completed',
            notification_message=f'Sales order {sales_order.reference} was completed.',
            notification_action_url='/order/sales',
        )

        self._log_activity('COMPLETE', sales_order, {'complete_date': sales_order.complete_date})
        self._invalidate_cache()
        return Response(SalesOrderDetailSerializer(sales_order, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        sales_order = self.get_object()
        if sales_order.status in [SalesOrderStatus.CANCELLED, SalesOrderStatus.COMPLETED]:
            return Response(
                {'error': 'Cannot cancel a completed or already cancelled sales order'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sales_order.line_items.filter(shipped_quantity__gt=0).exists():
            return Response(
                {'error': 'Cannot cancel a sales order after stock has already been shipped'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_user_id = get_request_user_id(request, as_str=False)
        profile_id = get_request_profile_id(request, required=True, as_str=False)

        try:
            with transaction.atomic():
                before = serialize_sales_order(sales_order)
                reservations = StockReservation.objects.select_related(
                    'stock_location',
                    'stock_lot',
                    'inventory_item',
                ).filter(
                    profile_id=profile_id,
                    external_order_type='sales_order_line',
                    external_order_id=str(sales_order.id),
                    status__in=[StockReservationStatus.ACTIVE, StockReservationStatus.PARTIALLY_FULFILLED],
                )
                for reservation in reservations:
                    remaining_quantity = Decimal(str(reservation.remaining_quantity))
                    if remaining_quantity <= 0:
                        continue
                    StockDomainService.release_reservation(
                        reservation=reservation,
                        quantity=remaining_quantity,
                        actor_user_id=current_user_id,
                        notes=f"Cancelled sales order {sales_order.reference}",
                    )
                    line_item = sales_order.line_items.get(id=reservation.external_order_line_id)
                    line_item.reserved_quantity = max(
                        Decimal(str(line_item.reserved_quantity)) - remaining_quantity,
                        Decimal('0'),
                    )
                    line_item.updated_by_user_id = current_user_id
                    line_item.save()

                sales_order.status = SalesOrderStatus.CANCELLED
                sales_order.updated_by_user_id = current_user_id
                sales_order.notes = request.data.get('notes', sales_order.notes)
                sales_order.save()
                after = serialize_sales_order(sales_order)
                publish_order_admin_event(
                    event_name='sales_order.cancelled',
                    payload=after,
                    actor=_audit_actor_from_request(request),
                    target={
                        'type': 'sales_order',
                        'id': str(sales_order.id),
                        'label': sales_order.reference,
                    },
                    summary=f'Sales order cancelled: {sales_order.reference}.',
                    metadata={'notes': request.data.get('notes', '')},
                    before=before,
                    after=after,
                    severity='warning',
                    feature_area='sales_orders',
                    reference_number=sales_order.reference,
                    notification_category='sales_order',
                    notification_title=f'Sales order {sales_order.reference} cancelled',
                    notification_message=f'Sales order {sales_order.reference} was cancelled.',
                    notification_action_url='/order/sales',
                )

                self._log_activity('CANCEL', sales_order, {'notes': request.data.get('notes', '')})

            self._invalidate_cache()
            return Response(SalesOrderDetailSerializer(sales_order, context={'request': request}).data)
        except SalesOrderLineItem.DoesNotExist:
            return Response({'error': 'Sales order line item not found'}, status=status.HTTP_404_NOT_FOUND)
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error(f"Error cancelling sales order {sales_order.reference}: {str(exc)}")
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    def _log_activity(self, action, instance, details):
        try:
            current_user_id = get_request_user_id(self.request, as_str=False)
            if current_user_id:
                logger.info(
                    f"User {current_user_id} performed {action} "
                    f"on sales order {instance.reference}: {details}"
                )
        except Exception as exc:
            logger.error(f"Failed to log sales-order activity: {str(exc)}")

class ReturnOrderViewSet(BaseCachePermissionViewset):
    required_permission = UNIFIED_PERMISSION_DICT.get('return_order')
    queryset = ReturnOrder.objects.select_related(
        'purchase_order',
        'purchase_order__supplier',
        'contact',
        'address',
    ).prefetch_related(
        'line_items',
        'line_items__original_line_item',
        'line_items__original_line_item__inventory_item',
    )
    filterset_fields = ['status', 'purchase_order']
    search_fields = ['reference', 'purchase_order__reference']
    ordering_fields = ['reference', 'created_at', 'issue_date', 'complete_date']
    ordering = ['-created_at']
    http_method_names = ['get', 'post', 'head', 'options']

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

        date_from = self.request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)

        date_to = self.request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return ReturnOrderListSerializer
        if self.action == 'dispatch_return_order':
            return ReturnOrderProcessSerializer
        return ReturnOrderDetailSerializer

    @action(
        detail=True,
        methods=["get"],
        url_path="notification-pdf",
        url_name="notification-pdf",
        authentication_classes=[],
        permission_classes=[AllowAny],
    )
    def notification_pdf(self, request, pk=None):
        """Serve one signed, short-lived return-order PDF to the notification delivery service."""
        token = str(request.query_params.get("token") or "")
        try:
            verify_return_order_pdf_token(return_order_id=str(pk), token=token)
            return_order = self.get_queryset().get(pk=pk)
            pdf_content = PDFService.generate_return_order_pdf(return_order)
        except NotificationDocumentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ReturnOrder.DoesNotExist:
            return Response({"detail": "Return order not found."}, status=status.HTTP_404_NOT_FOUND)
        except PDFServiceUnavailableError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        response = HttpResponse(pdf_content.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="Return_{return_order.reference}.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response

    def create(self, request, *args, **kwargs):
        return Response(
            {'error': 'Create return orders from the purchase-order flow'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

    @action(detail=True, methods=['post'], url_path='dispatch', url_name='dispatch')
    def dispatch_return_order(self, request, pk=None):
        return_order = self.get_object()
        if return_order.status not in [
            ReturnOrderStatus.PENDING,
            ReturnOrderStatus.AWAITING_PICKUP,
            ReturnOrderStatus.IN_TRANSIT,
        ]:
            return Response(
                {'error': 'Only pending, awaiting pickup, or in-transit return orders can be dispatched'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        current_user_id = get_request_user_id(request, as_str=False)
        profile_id = get_request_profile_id(request, required=True, as_str=False)
        structural_scope_location_id = _resolve_structural_scope_location_id(request, payload)

        try:
            with transaction.atomic():
                before = serialize_return_order(return_order)
                processed_count = 0
                processed_summaries = []
                for item in payload['return_items']:
                    try:
                        return_line = return_order.line_items.select_related(
                            'original_line_item',
                            'original_line_item__inventory_item',
                        ).get(id=item['return_line_item_id'])
                    except ReturnOrderLineItem.DoesNotExist:
                        raise ValueError(f"Return line item {item['return_line_item_id']} not found")

                    default_issue_quantity = (
                        Decimal('1')
                        if item.get('stock_serial_id') or item.get('serial_number')
                        else return_line.remaining_quantity
                    )
                    issue_quantity = Decimal(str(item.get('quantity', default_issue_quantity)))
                    if issue_quantity <= 0:
                        raise ValueError("Issue quantity must be greater than zero")
                    if issue_quantity > return_line.remaining_quantity:
                        raise ValueError(
                            f"Cannot dispatch {issue_quantity}; only {return_line.remaining_quantity} remains on return line {return_line.id}"
                        )

                    stock_location = scope_queryset_by_identity(
                        StockLocation.objects.filter(id=item['location_id']),
                        canonical_field='profile_id',
                        legacy_field='profile',
                        value=profile_id,
                    ).first()
                    if stock_location is None:
                        raise ValueError(f"Stock location {item['location_id']} not found")
                    _assert_location_within_scope(
                        profile_id=profile_id,
                        structural_scope_location_id=structural_scope_location_id,
                        stock_location=stock_location,
                        label=f"Return dispatch location {item['location_id']}",
                    )

                    stock_lot = None
                    stock_lot_id = item.get('stock_lot_id')
                    if stock_lot_id:
                        stock_lot = StockLot.objects.filter(profile_id=profile_id, id=stock_lot_id).first()
                        if stock_lot is None:
                            raise ValueError(f"Stock lot {stock_lot_id} not found")

                    stock_serial = None
                    stock_serial_id = item.get('stock_serial_id')
                    if stock_serial_id:
                        stock_serial = StockSerial.objects.filter(profile_id=profile_id, id=stock_serial_id).first()
                        if stock_serial is None:
                            raise ValueError(f"Stock serial {stock_serial_id} not found")

                    original_line = return_line.original_line_item
                    StockDomainService.issue_stock(
                        inventory_item=original_line.inventory_item,
                        stock_location=stock_location,
                        quantity=issue_quantity,
                        actor_user_id=current_user_id,
                        stock_lot=stock_lot,
                        stock_serial=stock_serial,
                        serial_number=item.get('serial_number', ''),
                        reference_type='return_order_line',
                        reference_id=str(return_line.id),
                        notes=item.get('notes') or payload.get('notes', '') or f"Supplier return {return_order.reference}",
                        movement_type=StockMovementType.RETURN_OUT,
                        tracking_type=TrackingType.SHIPPED,
                    )

                    return_line.quantity_processed = Decimal(str(return_line.quantity_processed)) + issue_quantity
                    return_line.updated_by_user_id = current_user_id
                    return_line.save()
                    processed_count += 1
                    processed_summaries.append({
                        'return_order_line_item_id': str(return_line.id),
                        'inventory_item_id': str(original_line.inventory_item_id),
                        'inventory_name': original_line.inventory_item.name_snapshot,
                        'inventory_barcode': original_line.inventory_item.barcode_snapshot or '',
                        'inventory_sku': original_line.inventory_item.sku_snapshot or '',
                        'quantity_processed': str(issue_quantity),
                        'stock_location_id': str(stock_location.id),
                        'stock_location_name': stock_location.name,
                        'lot_number': getattr(stock_lot, 'lot_number', '') or '',
                        'serial_number': getattr(stock_serial, 'serial_number', '') or '',
                    })

                return_order.status = ReturnOrderStatus.IN_TRANSIT
                if not return_order.issue_date:
                    return_order.issue_date = timezone.now()
                return_order.updated_by_user_id = current_user_id
                return_order.save()
                after = serialize_return_order(return_order)
                publish_order_admin_event(
                    event_name='return_order.dispatched',
                    payload=after,
                    actor=_audit_actor_from_request(request),
                    target={
                        'type': 'return_order',
                        'id': str(return_order.id),
                        'label': return_order.reference,
                    },
                    summary=f'Return order dispatched: {return_order.reference}.',
                    metadata={
                        'processed_count': processed_count,
                        'processed_lines': processed_summaries,
                        'notes': payload.get('notes', ''),
                    },
                    before=before,
                    after=after,
                    feature_area='supplier_returns',
                    reference_number=return_order.reference,
                    notification_category='purchase_order',
                    notification_title=f'Return order {return_order.reference} dispatched',
                    notification_message=f'Return order {return_order.reference} was dispatched.',
                    notification_action_url='/order/purchase',
                )

                self._log_activity('DISPATCH_RETURN', return_order, {
                    'processed_lines': processed_count,
                    'notes': payload.get('notes', ''),
                })

            self._invalidate_cache()
            return Response({
                'message': 'Return order dispatched successfully',
                'status': return_order.status,
                'processed_count': processed_count,
                'issue_date': return_order.issue_date,
            })
        except StockDomainError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.error(f"Error dispatching return order {return_order.reference}: {str(exc)}")
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        return_order = self.get_object()
        if return_order.status != ReturnOrderStatus.IN_TRANSIT:
            return Response(
                {'error': 'Only in-transit return orders can be completed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if return_order.line_items.filter(quantity_processed__lt=F('quantity_returned')).exists():
            return Response(
                {'error': 'All return line items must be fully dispatched before completion'},
                status=status.HTTP_400_BAD_REQUEST
            )

        before = serialize_return_order(return_order)
        return_order.status = ReturnOrderStatus.COMPLETED
        return_order.complete_date = timezone.now()
        return_order.updated_by_user_id = get_request_user_id(request, as_str=False)
        return_order.save()
        after = serialize_return_order(return_order)
        publish_order_admin_event(
            event_name='return_order.completed',
            payload=after,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'return_order',
                'id': str(return_order.id),
                'label': return_order.reference,
            },
            summary=f'Return order completed: {return_order.reference}.',
            metadata={'status': return_order.status},
            before=before,
            after=after,
            feature_area='supplier_returns',
            reference_number=return_order.reference,
            notification_category='purchase_order',
            notification_title=f'Return order {return_order.reference} completed',
            notification_message=f'Return order {return_order.reference} was completed.',
            notification_action_url='/order/purchase',
        )

        self._log_activity('COMPLETE_RETURN', return_order, {
            'completed_at': return_order.complete_date,
        })

        self._invalidate_cache()
        serializer = ReturnOrderDetailSerializer(return_order, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        return_order = self.get_object()
        if return_order.status in [ReturnOrderStatus.COMPLETED, ReturnOrderStatus.CANCELLED]:
            return Response(
                {'error': 'Cannot cancel a completed or already cancelled return order'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if return_order.line_items.filter(quantity_processed__gt=0).exists():
            return Response(
                {'error': 'Cannot cancel a return order after stock has already been dispatched'},
                status=status.HTTP_400_BAD_REQUEST
            )

        before = serialize_return_order(return_order)
        return_order.status = ReturnOrderStatus.CANCELLED
        return_order.updated_by_user_id = get_request_user_id(request, as_str=False)
        return_order.notes = request.data.get('notes', return_order.notes)
        return_order.save()
        after = serialize_return_order(return_order)
        publish_order_admin_event(
            event_name='return_order.cancelled',
            payload=after,
            actor=_audit_actor_from_request(request),
            target={
                'type': 'return_order',
                'id': str(return_order.id),
                'label': return_order.reference,
            },
            summary=f'Return order cancelled: {return_order.reference}.',
            metadata={'notes': request.data.get('notes', '')},
            before=before,
            after=after,
            severity='warning',
            feature_area='supplier_returns',
            reference_number=return_order.reference,
            notification_category='purchase_order',
            notification_title=f'Return order {return_order.reference} cancelled',
            notification_message=f'Return order {return_order.reference} was cancelled.',
            notification_action_url='/order/purchase',
        )

        self._log_activity('CANCEL_RETURN', return_order, {
            'notes': request.data.get('notes', ''),
        })

        self._invalidate_cache()
        serializer = ReturnOrderDetailSerializer(return_order, context={'request': request})
        return Response(serializer.data)

    def _log_activity(self, action, instance, details):
        try:
            current_user_id = get_request_user_id(self.request, as_str=False)
            if current_user_id:
                logger.info(
                    f"User {current_user_id} performed {action} "
                    f"on return order {instance.reference}: {details}"
                )
        except Exception as exc:
            logger.error(f"Failed to log return-order activity: {str(exc)}")

class LineItemsViewset(HasModelRequestPermission,viewsets.ModelViewSet):
    queryset=PurchaseOrderLineItem.objects.all()
    serializer_class=PurchaseOrderLineItemSerializer

    
