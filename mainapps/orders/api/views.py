from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Sum, Count, Avg, F, Case, When, Value, IntegerField
from django.db import transaction
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
import logging

from mainapps.inventory.models import InventoryTransaction, TransactionType
from mainapps.orders.api.serializers import PurchaseOrderDetailSerializer,PurchaseOrderAnalyticsSerializer, PurchaseOrderLineItemSerializer,PurchaseOrderLineItemCreateSerializer, PurchaseOrderListSerializer,ReceiveItemsSerializer
from mainapps.stock.models import StockItem
from subapps.permissions.constants import PURCHASE_ORDER_PERMISSIONS, UNIFIED_PERMISSION_DICT
from subapps.permissions.microservice_permissions import BaseCachePermissionViewset, HasModelRequestPermission, PermissionRequiredMixin
from subapps.services.emails.email_services import EmailService
from subapps.services.pdf.pdf_service import PDFService
from subapps.services.microservices.user_service import UserService

from ..models import (
    PurchaseOrder, PurchaseOrderLineItem, PurchaseOrderStatus,
    ReturnOrder, ReturnOrderLineItem, ReturnOrderStatus,
)
logger = logging.getLogger(__name__)

class PurchaseOrderViewSet(BaseCachePermissionViewset):
    """
    Enhanced ViewSet for comprehensive purchase order management
    Includes workflow management, receiving, returns, and analytics
    """
    required_permission = UNIFIED_PERMISSION_DICT.get('purchase_order')

    queryset = PurchaseOrder.objects.select_related('supplier', 'contact', 'address').prefetch_related('line_items')
    permission_classes = [IsAuthenticated, HasModelRequestPermission]
    
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
        profile_id = self.request.headers.get('X-Profile-ID')
        if profile_id:
            queryset = queryset.filter(profile=profile_id)

        else:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        
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
        print(queryset)
        return queryset
    
    def perform_create(self, serializer):
        """Set created_by and profile on creation"""
        current_user_id= self.request.user.id
        profile_id = self.request.headers.get('X-Profile-ID')
        
        extra_fields = {
            'status': PurchaseOrderStatus.PENDING,
        }
        
        if current_user_id:
            extra_fields['created_by'] = current_user_id
            extra_fields['responsible'] = current_user_id
        if profile_id:
            extra_fields['profile'] = profile_id
            
        instance = serializer.save(**extra_fields)
        
        # Log activity
        self._log_activity('CREATE', instance, {
            'initial_data': self.request.data,
            'created_data': serializer.data
        })
    
    def perform_update(self, serializer):
        """Set modified_by on update and log changes"""
        current_user_id= self.request.user.id
        original_data = self.get_serializer(serializer.instance).data
        
        extra_fields = {}
        if current_user_id:
            extra_fields['modified_by'] = current_user_id
        
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
            self._recalculate_order_total(purchase_order)
            
            # Log activity
            self._log_activity('ADD_LINE_ITEM', purchase_order, {
                'line_item_id': line_item.id,
                'quantity': line_item.quantity,
                'unit_price': str(line_item.unit_price)
            })
            
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
            self._recalculate_order_total(purchase_order)
            
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
        self._recalculate_order_total(purchase_order)
        
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
            purchase_order.approved_by = request.user.id
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
        
        
        current_user = UserService.get_current_user(request)
        
        try:
            with transaction.atomic():
                # Calculate total price
                total_price =purchase_order.total_price
                
                purchase_order.status = PurchaseOrderStatus.ISSUED
                purchase_order.issue_date = timezone.now()
                purchase_order.issued_by = current_user_id
                purchase_order.save()
                
                # Send email notification if requested
                email_sent = False
                if serializer.validated_data.get('notify_supplier', True):
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
        
        current_user = UserService.get_current_user(request)
        
        with transaction.atomic():
            purchase_order.status = PurchaseOrderStatus.RECEIVED
            purchase_order.received_date = timezone.now()
            purchase_order.received_by = current_user_id
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
        current_user = UserService.get_current_user(request)
        
        try:
            with transaction.atomic():
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
                    total_q_r=line_item.quantity_received+ quantity_received
                    # Validate quantity
                    if total_q_r > line_item.quantity :
                        raise ValueError(
                            f"Cannot receive {quantity_received} items,{line_item.quantity_received, 'received already' if line_item.quantity_received>0 else ''}"
                            f"only {line_item.quantity} ordered"
                        )
                    
                    # Create or update stock item
                    stock_item= line_item.stock_item
                    
                    if  stock_item:
                        stock_item.quantity = F('quantity') + quantity_received
                        stock_item.save()
                        stock_item.refresh_from_db()
                    
                    
                    # Create tracking record
                    StockItemTracking.objects.create(
                        inventory=stock_item.inventory,
                        item=stock_item,
                        tracking_type=10,  # RECEIVED
                        notes=f"Received {quantity_received} units from PO {purchase_order.reference}",
                        user=current_user_id,
                        deltas={
                            'quantity_received': float(quantity_received),
                            'purchase_price': float(line_item.unit_price),
                            'line_item_id': line_item_id
                        }
                    )
                    
                    # Update line item received quantity
                    line_item.quantity_received = F('quantity_received') + quantity_received
                    line_item.save()
                    
                    received_count += 1
                
                # Update order status if not already received
                if purchase_order.status == PurchaseOrderStatus.ISSUED:
                    purchase_order.status = PurchaseOrderStatus.RECEIVED
                    purchase_order.received_date = timezone.now()
                    purchase_order.received_by = current_user_id
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
                    'order_status': purchase_order.status
                })
                
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
        
        current_user = UserService.get_current_user(request)
        
        try:
            with transaction.atomic():
                purchase_order.status = PurchaseOrderStatus.COMPLETED
                purchase_order.complete_date = timezone.now()
                purchase_order.completed_by = current_user_id
                purchase_order.save()
                
                # Create inventory transactions for audit
                self._create_inventory_transactions(purchase_order, current_user)
                
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
        
        
        current_user = UserService.get_current_user(request)
        
        with transaction.atomic():
            purchase_order.status = PurchaseOrderStatus.CANCELLED
            purchase_order.cancelled_by = current_user_id
            purchase_order.cancelled_at = timezone.now()
            purchase_order.cancellation_reason = serializer.validated_data.get('notes', '')
            purchase_order.save()
            
            # Log activity
            self._log_activity('CANCEL', purchase_order, {
                'cancelled_by': current_user.get('full_name'),
                'reason': serializer.validated_data.get('notes', '')
            })
        
        return Response({
            'message': 'Purchase order cancelled successfully',
            'status': purchase_order.status,
            'cancelled_at': purchase_order.cancelled_at
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
        current_user = UserService.get_current_user(request)
        
        try:
            with transaction.atomic():
                # Create return order
                return_order = ReturnOrder.objects.create(
                    purchase_order=purchase_order,
                    profile=purchase_order.profile,
                    contact=purchase_order.contact,
                    address=purchase_order.address,
                    status=ReturnOrderStatus.PENDING,
                    return_reason=return_reason,
                    created_by=current_user_id,
                    responsible=current_user_id
                )
                
                # Create return line items
                for item in return_items:
                    try:
                        line_item = purchase_order.line_items.get(id=item['line_item_id'])
                    except PurchaseOrderLineItem.DoesNotExist:
                        raise ValueError(f"Line item {item['line_item_id']} not found")
                    
                    # Validate return quantity
                    if item['quantity'] > line_item.quantity:
                        raise ValueError(
                            f"Cannot return {item['quantity']} items, "
                            f"only {line_item.quantity} were ordered"
                        )
                    
                    ReturnOrderLineItem.objects.create(
                        return_order=return_order,
                        original_line_item=line_item,
                        quantity_returned=item['quantity'],
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
        financial_metrics = queryset.aggregate(
            total_value=Sum('total_price'),
            average_value=Avg('total_price')
        )
        
        total_value = financial_metrics['total_value'] or 0
        average_value = financial_metrics['average_value'] or 0
        
        # Convert from cents to currency
        total_value = Decimal(total_value) / 100
        average_value = Decimal(average_value) / 100
        
        # Time-based analytics
        monthly_trends = self._get_monthly_trends(queryset)
        weekly_trends = self._get_weekly_trends(queryset)
        
        # Supplier analytics
        supplier_performance = self._get_supplier_performance(queryset)
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
            'total_value_this_month': queryset.filter(
                created_at__gte=month_ago
            ).aggregate(total=Sum('total_price'))['total'] or 0,
        }
        
        # Convert total value from cents
        summary['total_value_this_month'] = Decimal(summary['total_value_this_month']) / 100
        
        return Response(summary)
    
    # ==================== HELPER METHODS ====================
    
    def _recalculate_order_total(self, purchase_order):
        """Recalculate and update purchase order total"""
        total = sum(
            line_item.total_price for line_item in purchase_order.line_items.all()
        )
        purchase_order.total_price = int(total * 100)  # Store as cents
        purchase_order.save(update_fields=['total_price'])
    
    def _create_inventory_transactions(self, purchase_order, current_user_id):
        """Create inventory transaction records for audit"""
        transactions = []
        for line_item in purchase_order.line_items.all():
            if line_item.stock_item:
                transactions.append(
                    InventoryTransaction(
                        item=line_item.stock_item,
                        quantity=line_item.quantity if line_item.quantity_received<=0 else line_item.quantity_received,
                        unit_price=line_item.unit_price,
                        transaction_type=TransactionType.PO_COMPLETE,
                        reference=purchase_order.reference,
                        user=current_user_id,
                        profile=purchase_order.profile,
                        notes=f"Completed from PO {purchase_order.reference}"
                    )
                )
        
        if transactions:
            InventoryTransaction.objects.bulk_create(transactions)
    
    def _get_monthly_trends(self, queryset):
        """Get monthly trends for the last 12 months"""
        trends = []
        for i in range(12):
            month_start = timezone.now().replace(day=1) - timedelta(days=30*i)
            month_end = month_start + timedelta(days=30)
            
            month_data = queryset.filter(
                created_at__gte=month_start,
                created_at__lt=month_end
            ).aggregate(
                count=Count('id'),
                total_value=Sum('total_price')
            )
            
            trends.append({
                'month': month_start.strftime('%Y-%m'),
                'count': month_data['count'],
                'total_value': Decimal(month_data['total_value'] or 0) / 100
            })
        
        return trends
    
    def _get_weekly_trends(self, queryset):
        """Get weekly trends for the last 8 weeks"""
        trends = []
        for i in range(8):
            week_start = timezone.now().date() - timedelta(days=7*i)
            week_end = week_start + timedelta(days=7)
            
            week_data = queryset.filter(
                created_at__date__gte=week_start,
                created_at__date__lt=week_end
            ).aggregate(
                count=Count('id'),
                total_value=Sum('total_price')
            )
            
            trends.append({
                'week': week_start.strftime('%Y-W%U'),
                'count': week_data['count'],
                'total_value': Decimal(week_data['total_value'] or 0) / 100
            })
        
        return trends
    
    def _get_supplier_performance(self, queryset):
        """Get supplier performance metrics"""
        return list(queryset.values(
            'supplier__name'
        ).annotate(
            order_count=Count('id'),
            total_value=Sum('total_price'),
            avg_delivery_time=Avg(
                Case(
                    When(
                        delivery_date__isnull=False,
                        issue_date__isnull=False,
                        then=F('delivery_date') - F('issue_date')
                    ),
                    default=Value(0)
                )
            ),
            on_time_deliveries=Count(
                Case(
                    When(
                        received_date__lte=F('delivery_date'),
                        then=1
                    )
                )
            )
        ).order_by('-total_value')[:10])
    
    def _get_top_suppliers_by_value(self, queryset):
        """Get top suppliers by order value"""
        return list(queryset.values(
            'supplier__name', 'supplier__id'
        ).annotate(
            total_value=Sum('total_price'),
            order_count=Count('id')
        ).order_by('-total_value')[:5])
    
    def _get_performance_metrics(self, queryset):
        """Calculate performance metrics"""
        completed_orders = queryset.filter(status=PurchaseOrderStatus.COMPLETED)
        
        # Average processing time (from creation to completion)
        avg_processing_time = completed_orders.aggregate(
            avg_time=Avg(
                Case(
                    When(
                        complete_date__isnull=False,
                        then=F('complete_date') - F('created_at')
                    ),
                    default=Value(0)
                )
            )
        )['avg_time']
        
        # Average delivery time (from issue to delivery)
        avg_delivery_time = completed_orders.aggregate(
            avg_time=Avg(
                Case(
                    When(
                        received_date__isnull=False,
                        issue_date__isnull=False,
                        then=F('received_date') - F('issue_date')
                    ),
                    default=Value(0)
                )
            )
        )['avg_time']
        
        # On-time delivery rate
        total_delivered = completed_orders.filter(
            received_date__isnull=False,
            delivery_date__isnull=False
        ).count()
        
        on_time_delivered = completed_orders.filter(
            received_date__lte=F('delivery_date')
        ).count()
        
        on_time_rate = (on_time_delivered / total_delivered * 100) if total_delivered > 0 else 0
        
        # Financial metrics
        total_savings = Decimal('0.00')  # Calculate based on your business logic
        avg_cost_per_order = queryset.aggregate(
            avg_cost=Avg('total_price')
        )['avg_cost'] or 0
        
        return {
            'average_processing_time': avg_processing_time.days if avg_processing_time else 0,
            'average_delivery_time': avg_delivery_time.days if avg_delivery_time else 0,
            'on_time_delivery_rate': round(on_time_rate, 2),
            'total_savings': total_savings,
            'cost_per_order': Decimal(avg_cost_per_order) / 100
        }
    
    def _log_activity(self, action, instance, details):
        """Log user activity for audit trail"""
        try:
            current_user_id= self.request.user.id
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
            current_user = UserService.get_current_user(request)
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
        except Exception as e:
            logger.error(f"Failed to resend email: {str(e)}")
            return Response(
                {'error': f'Failed to resend email: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

# we need to create mcp tools that agent can use to search for products on various