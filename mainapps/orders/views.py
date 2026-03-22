from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Sum, Count, Avg, F, Case, When, Value, IntegerField, DecimalField
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
import logging

from mainapps.orders.serializers import (
    PurchaseOrderDetailSerializer,
    PurchaseOrderAnalyticsSerializer,
    PurchaseOrderLineItemSerializer,
    PurchaseOrderLineItemCreateSerializer,
    PurchaseOrderListSerializer,
    ReceiveItemsSerializer,
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
from subapps.permissions.constants import PURCHASE_ORDER_PERMISSIONS, UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset, HasModelRequestPermission, PermissionRequiredMixin
from subapps.services.emails.email_services import EmailService
from subapps.services.pdf.pdf_service import PDFService, PDFServiceUnavailableError
from subapps.services.identity_directory import IdentityDirectory
from subapps.services.stock_domain import StockDomainError, StockDomainService
from subapps.utils.request_context import (
    get_request_profile_id,
    get_request_user_id,
    scope_queryset_by_identity,
)

from .models import (
    PurchaseOrder, PurchaseOrderLineItem, PurchaseOrderStatus,
    ReturnOrder, ReturnOrderLineItem, ReturnOrderStatus,
    SalesOrder, SalesOrderLineItem, SalesOrderShipment, SalesOrderStatus,
)
logger = logging.getLogger(__name__)

class PurchaseOrderViewSet(BaseCachePermissionViewset):
    """
    Enhanced ViewSet for comprehensive purchase order management
    Includes workflow management, receiving, returns, and analytics
    """
    required_permission = UNIFIED_PERMISSION_DICT.get('purchase_order')

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
        
        # Log activity
        self._log_activity('CREATE', instance, {
            'initial_data': self.request.data,
            'created_data': serializer.data
        })
    
    def perform_update(self, serializer):
        """Set modified_by on update and log changes"""
        current_user_id = get_request_user_id(self.request, as_str=False)
        original_data = self.get_serializer(serializer.instance).data
        
        extra_fields = {}
        if current_user_id:
            extra_fields['updated_by_user_id'] = current_user_id
        
        instance = serializer.save(**extra_fields)
        
        # Log activity
        self._log_activity('UPDATE', instance, {
            'changes': self._get_field_changes(original_data, serializer.data)
        })
    
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
            serializer.save()
            
            # Recalculate order total
            
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['delete'])
    def remove_line_item(self, request, pk=None):
        """Remove a line item from purchase order"""
        purchase_order = self.get_object()
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
            purchase_order.status = PurchaseOrderStatus.APPROVED
            purchase_order.approved_by_user_id = get_request_user_id(request, required=True, as_str=False)
            purchase_order.approved_at = timezone.now()
            purchase_order.save()
            
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
                # Calculate total price
                total_price =purchase_order.total_price
                
                purchase_order.status = PurchaseOrderStatus.ISSUED
                purchase_order.issue_date = timezone.now()
                purchase_order.updated_by_user_id = current_user_id
                purchase_order.save()
                
                # Send email notification if requested
                email_sent = False
                if request.data.get('notify_supplier', True):
                    try:
                        self._send_purchase_order_email(purchase_order)
                        email_sent = True
                    except Exception as e:
                        logger.warning(f"Failed to send email for PO {purchase_order.reference}: {str(e)}")
                        # Don't fail the entire operation if email fails
                
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
            purchase_order.status = PurchaseOrderStatus.RECEIVED
            purchase_order.received_date = timezone.now()
            purchase_order.received_by_user_id = current_user_id
            purchase_order.save()
            
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
        
        try:
            with transaction.atomic():
                goods_receipt = StockDomainService.create_goods_receipt(
                    purchase_order=purchase_order,
                    actor_user_id=current_user_id,
                    notes=request.data.get('notes', ''),
                )
                received_count = 0
                
                for item_data in received_items:
                    line_item_id = item_data['line_item_id']
                    quantity_received = item_data['quantity_received']
                    location_id = item_data['location_id']
                    
                    # Get line item
                    try:
                        line_item = purchase_order.line_items.get(id=line_item_id)
                    except PurchaseOrderLineItem.DoesNotExist:
                        raise ValueError(f"Line item {line_item_id} not found")
                    stock_location = scope_queryset_by_identity(
                        StockLocation.objects.filter(id=location_id),
                        canonical_field='profile_id',
                        legacy_field='profile',
                        value=profile_id,
                    ).first()
                    if stock_location is None:
                        raise ValueError(f"Stock location {location_id} not found")

                    StockDomainService.receive_purchase_line(
                        purchase_order=purchase_order,
                        line_item=line_item,
                        stock_location=stock_location,
                        quantity_received=quantity_received,
                        actor_user_id=current_user_id,
                        goods_receipt=goods_receipt,
                        lot_number=item_data.get('lot_number', ''),
                        manufactured_date=item_data.get('manufactured_date'),
                        expiry_date=item_data.get('expiry_date'),
                        serial_numbers=item_data.get('serial_numbers'),
                        notes=item_data.get('notes') or request.data.get('notes', ''),
                    )
                    
                    received_count += 1
                
                # Update order status if not already received
                if purchase_order.status == PurchaseOrderStatus.ISSUED:
                    purchase_order.status = PurchaseOrderStatus.RECEIVED
                    purchase_order.received_date = timezone.now()
                    purchase_order.received_by_user_id = current_user_id
                    purchase_order.save()
                
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
        
        current_user = IdentityDirectory.get_current_user(request) or {}
        current_user_id = current_user.get('id')
        
        try:
            with transaction.atomic():
                purchase_order.status = PurchaseOrderStatus.COMPLETED
                purchase_order.complete_date = timezone.now()
                purchase_order.updated_by_user_id = current_user_id
                purchase_order.save()
                
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
            purchase_order.status = PurchaseOrderStatus.CANCELLED
            purchase_order.updated_by_user_id = current_user_id
            purchase_order.notes = request.data.get('notes', purchase_order.notes)
            purchase_order.save()
            
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
                
                # Send notifications if requested
                try:
                    self._send_return_order_email(return_order, purchase_order)
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
            'pending_approval': queryset.filter(status=PurchaseOrderStatus.PENDING).count(),
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
                d["avg_delivery_time"].append(order.delivery_date - order.issue_date)

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
            (o.received_date - o.issue_date)
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
    
    def _send_purchase_order_email(self, purchase_order):
        """Send purchase order email to supplier using enhanced service"""
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
    
    def _send_return_order_email(self, return_order, purchase_order):
        """Send return order email notifications using enhanced service"""
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
            logger.error(f"Failed to generate PDF for PO {purchase_order.reference}: {str(e)}")
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
            
            self._send_purchase_order_email(purchase_order)
            
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
        'line_items__inventory',
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
        self._log_activity('CREATE', instance, {'initial_data': self.request.data})

    def perform_update(self, serializer):
        current_user_id = get_request_user_id(self.request, as_str=False)
        extra_fields = {}
        if current_user_id:
            extra_fields['updated_by_user_id'] = current_user_id
        instance = serializer.save(**extra_fields)
        self._log_activity('UPDATE', instance, {'updated_fields': list(serializer.validated_data.keys())})

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
        self._log_activity('ADD_LINE_ITEM', sales_order, {'line_item_id': str(line_item.id)})
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
        line_item = serializer.save()
        self._log_activity('UPDATE_LINE_ITEM', sales_order, {'line_item_id': str(line_item.id)})
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

        line_item.delete()
        self._log_activity('REMOVE_LINE_ITEM', sales_order, {'line_item_id': str(line_item_id)})
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

        try:
            with transaction.atomic():
                reservations = []
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

                if sales_order.status == SalesOrderStatus.PENDING:
                    sales_order.status = SalesOrderStatus.IN_PROGRESS
                    sales_order.updated_by_user_id = current_user_id
                    sales_order.save()

                self._log_activity('RESERVE_STOCK', sales_order, {
                    'reservation_count': len(reservations),
                    'reservation_ids': reservations,
                })

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

        try:
            with transaction.atomic():
                released_count = 0
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

                if (
                    sales_order.status == SalesOrderStatus.IN_PROGRESS
                    and not sales_order.line_items.filter(
                        Q(reserved_quantity__gt=0) | Q(shipped_quantity__gt=0)
                    ).exists()
                ):
                    sales_order.status = SalesOrderStatus.PENDING
                    sales_order.updated_by_user_id = current_user_id
                    sales_order.save()

                self._log_activity('RELEASE_RESERVATION', sales_order, {'released_count': released_count})

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

        try:
            with transaction.atomic():
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

                self._log_activity('SHIP', sales_order, {
                    'shipment_reference': shipment.reference,
                    'shipment_line_count': shipment_line_count,
                })

            shipment.refresh_from_db()
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

        sales_order.status = SalesOrderStatus.COMPLETED
        sales_order.complete_date = timezone.now()
        sales_order.updated_by_user_id = get_request_user_id(request, as_str=False)
        sales_order.save()

        self._log_activity('COMPLETE', sales_order, {'complete_date': sales_order.complete_date})
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

                self._log_activity('CANCEL', sales_order, {'notes': request.data.get('notes', '')})

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
        return queryset

    def get_serializer_class(self):
        if self.action == 'list':
            return ReturnOrderListSerializer
        if self.action == 'dispatch':
            return ReturnOrderProcessSerializer
        return ReturnOrderDetailSerializer

    def create(self, request, *args, **kwargs):
        return Response(
            {'error': 'Create return orders from the purchase-order flow'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

    @action(detail=True, methods=['post'])
    def dispatch(self, request, pk=None):
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

        try:
            with transaction.atomic():
                processed_count = 0
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

                return_order.status = ReturnOrderStatus.IN_TRANSIT
                if not return_order.issue_date:
                    return_order.issue_date = timezone.now()
                return_order.updated_by_user_id = current_user_id
                return_order.save()

                self._log_activity('DISPATCH_RETURN', return_order, {
                    'processed_lines': processed_count,
                    'notes': payload.get('notes', ''),
                })

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

        return_order.status = ReturnOrderStatus.COMPLETED
        return_order.complete_date = timezone.now()
        return_order.updated_by_user_id = get_request_user_id(request, as_str=False)
        return_order.save()

        self._log_activity('COMPLETE_RETURN', return_order, {
            'completed_at': return_order.complete_date,
        })

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

        return_order.status = ReturnOrderStatus.CANCELLED
        return_order.updated_by_user_id = get_request_user_id(request, as_str=False)
        return_order.notes = request.data.get('notes', return_order.notes)
        return_order.save()

        self._log_activity('CANCEL_RETURN', return_order, {
            'notes': request.data.get('notes', ''),
        })

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

    
