from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q, Sum, Count, Avg, F
from django.utils import timezone
from datetime import timedelta, datetime
from decimal import Decimal
from django.db import transaction
import requests
import json
import logging

from ..models import (
    POSConfiguration, POSTerminal, Customer, Table, POSSession,
    POSOrder, POSOrderItem, POSPayment, POSDiscount, POSReceipt, POSHoldOrder
)
from .serializers import (
    POSConfigurationSerializer, POSTerminalSerializer, CustomerSerializer,
    TableSerializer, POSSessionSerializer, POSOrderSerializer,
    POSOrderItemSerializer, POSPaymentSerializer, POSDiscountSerializer,
    POSReceiptSerializer, POSHoldOrderSerializer
)

logger = logging.getLogger(__name__)

class ProductServiceMixin:
    """Mixin to interact with Product Service"""
    
    def get_product_data(self, product_id=None, variant_id=None, search=None):
        """Get product data from Product Service"""
        try:
            base_url = "http://product-service:8000/product_api"  # Adjust URL as needed
            headers = {
                'X-Profile-ID': self.request.headers.get('X-Profile-ID'),
                'Authorization': self.request.headers.get('Authorization')
            }
            
            if variant_id:
                response = requests.get(f"{base_url}/variants/{variant_id}/", headers=headers)
            elif product_id:
                response = requests.get(f"{base_url}/products/{product_id}/", headers=headers)
            elif search:
                response = requests.get(f"{base_url}/products/pos_search/?q={search}", headers=headers)
            else:
                response = requests.get(f"{base_url}/products/pos_products/", headers=headers)
            
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"Error fetching product data: {str(e)}")
            return None

class POSConfigurationViewSet(viewsets.ModelViewSet):
    """POS Configuration management"""
    queryset = POSConfiguration.objects.all()
    serializer_class = POSConfigurationSerializer
    
    def get_queryset(self):
        profile_id = self.request.headers.get('X-Profile-ID')
        return self.queryset.filter(profile=profile_id)
    
    @action(detail=False, methods=['GET'])
    def current(self, request):
        """Get current POS configuration"""
        profile_id = request.headers.get('X-Profile-ID')
        config, created = POSConfiguration.objects.get_or_create(
            profile=profile_id,
            defaults={'created_by': str(request.user.id)}
        )
        serializer = self.get_serializer(config)
        return Response(serializer.data)

class POSTerminalViewSet(viewsets.ModelViewSet):
    """POS Terminal management"""
    queryset = POSTerminal.objects.all()
    serializer_class = POSTerminalSerializer
    
    def get_queryset(self):
        profile_id = self.request.headers.get('X-Profile-ID')
        return self.queryset.filter(profile=profile_id)

class CustomerViewSet(viewsets.ModelViewSet):
    """Customer management"""
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['name', 'email', 'phone']
    
    def get_queryset(self):
        profile_id = self.request.headers.get('X-Profile-ID')
        return self.queryset.filter(profile=profile_id)

class TableViewSet(viewsets.ModelViewSet):
    """Table management"""
    queryset = Table.objects.all()
    serializer_class = TableSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['number', 'name']
    filterset_fields = ['is_active']
    
    def get_queryset(self):
        profile_id = self.request.headers.get('X-Profile-ID')
        return self.queryset.filter(profile=profile_id)

class POSSessionViewSet(viewsets.ModelViewSet):
    """POS Session management"""
    queryset = POSSession.objects.all()
    serializer_class = POSSessionSerializer
    
    def get_queryset(self):
        profile_id = self.request.headers.get('X-Profile-ID')
        return self.queryset.filter(profile=profile_id)
    
    @action(detail=False, methods=['GET'])
    def current(self, request):
        """Get current open session"""
        profile_id = request.headers.get('X-Profile-ID')
        session = POSSession.objects.filter(
            profile=profile_id,
            status='open',
            user=str(request.user.id)
        ).first()
        
        if session:
            serializer = self.get_serializer(session)
            return Response(serializer.data)
        return Response({'detail': 'No open session found'}, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=False, methods=['POST'])
    def open_session(self, request):
        """Open a new POS session"""
        profile_id = request.headers.get('X-Profile-ID')
        
        # Check if user already has an open session
        existing_session = POSSession.objects.filter(
            profile=profile_id,
            user=str(request.user.id),
            status='open'
        ).first()
        
        if existing_session:
            return Response(
                {'detail': 'You already have an open session'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save(
                profile=profile_id,
                created_by=str(request.user.id),
                user=str(request.user.id)
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['POST'])
    def close_session(self, request, pk=None):
        """Close a POS session"""
        session = self.get_object()
        closing_balance = request.data.get('closing_balance')
        
        if not closing_balance:
            return Response(
                {'detail': 'Closing balance is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        session.close_session(Decimal(str(closing_balance)))
        serializer = self.get_serializer(session)
        return Response(serializer.data)

class POSOrderViewSet(ProductServiceMixin, viewsets.ModelViewSet):
    """POS Order management"""
    queryset = POSOrder.objects.all()
    serializer_class = POSOrderSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['order_number', 'customer__name']
    filterset_fields = ['status', 'session', 'customer', 'table']
    
    def get_queryset(self):
        profile_id = self.request.headers.get('X-Profile-ID')
        return self.queryset.filter(profile=profile_id).select_related(
            'customer', 'table', 'session'
        ).prefetch_related('items', 'payments')
    
    @action(detail=False, methods=['GET'])
    def current_draft(self, request):
        """Get current draft order for the session"""
        session_id = request.query_params.get('session_id')
        if not session_id:
            return Response(
                {'detail': 'Session ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        order = POSOrder.objects.filter(
            session_id=session_id,
            status='draft'
        ).first()
        
        if order:
            serializer = self.get_serializer(order)
            return Response(serializer.data)
        return Response({'detail': 'No draft order found'}, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=False, methods=['POST'])
    def create_or_get_draft(self, request):
        """Create or get existing draft order"""
        session_id = request.data.get('session_id')
        if not session_id:
            return Response(
                {'detail': 'Session ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Try to get existing draft order
        order = POSOrder.objects.filter(
            session_id=session_id,
            status='draft'
        ).first()
        
        if order:
            serializer = self.get_serializer(order)
            return Response(serializer.data)
        
        # Create new draft order
        profile_id = request.headers.get('X-Profile-ID')
        order = POSOrder.objects.create(
            session_id=session_id,
            profile=profile_id,
            created_by=str(request.user.id),
            customer_id=request.data.get('customer_id'),
            table_id=request.data.get('table_id')
        )
        
        serializer = self.get_serializer(order)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['POST'])
    def add_item(self, request, pk=None):
        """Add item to order"""
        order = self.get_object()
        variant_id = request.data.get('variant_id')
        quantity = request.data.get('quantity', 1)
        customizations = request.data.get('customizations', {})
        special_instructions = request.data.get('special_instructions', '')
        
        if not variant_id:
            return Response(
                {'detail': 'Variant ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get product data from Product Service
        variant_data = self.get_product_data(variant_id=variant_id)
        if not variant_data:
            return Response(
                {'detail': 'Product variant not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Create order item
        order_item = POSOrderItem.objects.create(
            order=order,
            product_variant_id=variant_id,
            product_name=variant_data.get('product_details', {}).get('name', ''),
            variant_name=variant_data.get('display_name', ''),
            quantity=Decimal(str(quantity)),
            unit_price=Decimal(str(variant_data.get('selling_price', 0))),
            tax_rate=Decimal(str(variant_data.get('product_details', {}).get('tax_rate', 0))),
            customizations=customizations,
            special_instructions=special_instructions
        )
        
        # Recalculate order totals
        order.calculate_totals()
        
        serializer = POSOrderItemSerializer(order_item)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['POST'])
    def update_item(self, request, pk=None):
        """Update order item"""
        order = self.get_object()
        item_id = request.data.get('item_id')
        quantity = request.data.get('quantity')
        
        try:
            item = order.items.get(id=item_id)
            if quantity is not None:
                item.quantity = Decimal(str(quantity))
                item.save()
            
            # Recalculate order totals
            order.calculate_totals()
            
            serializer = POSOrderItemSerializer(item)
            return Response(serializer.data)
        except POSOrderItem.DoesNotExist:
            return Response(
                {'detail': 'Order item not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=True, methods=['POST'])
    def remove_item(self, request, pk=None):
        """Remove item from order"""
        order = self.get_object()
        item_id = request.data.get('item_id')
        
        try:
            item = order.items.get(id=item_id)
            item.delete()
            
            # Recalculate order totals
            order.calculate_totals()
            
            return Response({'detail': 'Item removed successfully'})
        except POSOrderItem.DoesNotExist:
            return Response(
                {'detail': 'Order item not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=True, methods=['POST'])
    def apply_discount(self, request, pk=None):
        """Apply discount to order"""
        order = self.get_object()
        discount_amount = request.data.get('discount_amount', 0)
        discount_percent = request.data.get('discount_percent', 0)
        
        if discount_percent:
            order.discount_amount = order.subtotal * (Decimal(str(discount_percent)) / 100)
        else:
            order.discount_amount = Decimal(str(discount_amount))
        
        order.calculate_totals()
        
        serializer = self.get_serializer(order)
        return Response(serializer.data)
    
    @action(detail=True, methods=['POST'])
    def add_tip(self, request, pk=None):
        """Add tip to order"""
        order = self.get_object()
        tip_amount = request.data.get('tip_amount', 0)
        tip_percent = request.data.get('tip_percent', 0)
        
        if tip_percent:
            order.tip_amount = order.subtotal * (Decimal(str(tip_percent)) / 100)
        else:
            order.tip_amount = Decimal(str(tip_amount))
        
        order.calculate_totals()
        
        serializer = self.get_serializer(order)
        return Response(serializer.data)
    
    @action(detail=True, methods=['POST'])
    def process_payment(self, request, pk=None):
        """Process payment for order"""
        order = self.get_object()
        payments_data = request.data.get('payments', [])
        
        if not payments_data:
            return Response(
                {'detail': 'Payment data is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        with transaction.atomic():
            total_paid = Decimal('0.00')
            
            for payment_data in payments_data:
                payment = POSPayment.objects.create(
                    order=order,
                    payment_method=payment_data.get('payment_method'),
                    amount=Decimal(str(payment_data.get('amount', 0))),
                    cash_received=Decimal(str(payment_data.get('cash_received', 0))) if payment_data.get('cash_received') else None,
                    reference_number=payment_data.get('reference_number', ''),
                    profile=order.profile,
                    created_by=str(request.user.id)
                )
                payment.process_payment()
                total_paid += payment.amount
            
            # Check if order is fully paid
            if total_paid >= order.total_amount:
                order.complete_order()
                
                # Create receipt if requested
                if request.data.get('create_receipt', True):
                    receipt = POSReceipt.objects.create(
                        order=order,
                        profile=order.profile,
                        created_by=str(request.user.id)
                    )
                    
                    if request.data.get('print_receipt', False):
                        receipt.printed_at = timezone.now()
                        receipt.save()
                    
                    if request.data.get('email_receipt', False):
                        email_address = request.data.get('email_address')
                        if email_address:
                            receipt.email_address = email_address
                            receipt.emailed_at = timezone.now()
                            receipt.save()
        
        serializer = self.get_serializer(order)
        return Response(serializer.data)
    
    @action(detail=True, methods=['POST'])
    def hold_order(self, request, pk=None):
        """Hold order for later"""
        order = self.get_object()
        hold_reason = request.data.get('hold_reason', '')
        
        # Save order data
        order_data = {
            'order_id': str(order.id),
            'items': list(order.items.values()),
            'customer_id': str(order.customer.id) if order.customer else None,
            'table_id': str(order.table.id) if order.table else None,
            'subtotal': str(order.subtotal),
            'tax_amount': str(order.tax_amount),
            'discount_amount': str(order.discount_amount),
            'tip_amount': str(order.tip_amount),
            'total_amount': str(order.total_amount),
            'notes': order.notes
        }
        
        hold_order = POSHoldOrder.objects.create(
            order_data=order_data,
            hold_reason=hold_reason,
            held_by=str(request.user.id),
            profile=order.profile,
            created_by=str(request.user.id)
        )
        
        # Delete the draft order
        order.delete()
        
        serializer = POSHoldOrderSerializer(hold_order)
        return Response(serializer.data)
    
    @action(detail=False, methods=['GET'])
    def held_orders(self, request):
        """Get held orders"""
        profile_id = request.headers.get('X-Profile-ID')
        held_orders = POSHoldOrder.objects.filter(
            profile=profile_id,
            retrieved_at__isnull=True
        )
        serializer = POSHoldOrderSerializer(held_orders, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['POST'])
    def retrieve_held_order(self, request):
        """Retrieve a held order"""
        hold_order_id = request.data.get('hold_order_id')
        session_id = request.data.get('session_id')
        
        try:
            hold_order = POSHoldOrder.objects.get(id=hold_order_id)
            order_data = hold_order.order_data
            
            # Recreate the order
            order = POSOrder.objects.create(
                session_id=session_id,
                customer_id=order_data.get('customer_id'),
                table_id=order_data.get('table_id'),
                subtotal=Decimal(order_data.get('subtotal', '0')),
                tax_amount=Decimal(order_data.get('tax_amount', '0')),
                discount_amount=Decimal(order_data.get('discount_amount', '0')),
                tip_amount=Decimal(order_data.get('tip_amount', '0')),
                total_amount=Decimal(order_data.get('total_amount', '0')),
                notes=order_data.get('notes', ''),
                profile=hold_order.profile,
                created_by=str(request.user.id)
            )
            
            # Recreate order items
            for item_data in order_data.get('items', []):
                POSOrderItem.objects.create(
                    order=order,
                    product_variant_id=item_data['product_variant_id'],
                    product_name=item_data['product_name'],
                    variant_name=item_data['variant_name'],
                    quantity=Decimal(str(item_data['quantity'])),
                    unit_price=Decimal(str(item_data['unit_price'])),
                    tax_rate=Decimal(str(item_data['tax_rate'])),
                    customizations=item_data.get('customizations', {}),
                    special_instructions=item_data.get('special_instructions', ''),
                    profile=order.profile,
                    created_by=str(request.user.id)
                )
            
            # Mark hold order as retrieved
            hold_order.retrieve_order(str(request.user.id))
            
            serializer = self.get_serializer(order)
            return Response(serializer.data)
            
        except POSHoldOrder.DoesNotExist:
            return Response(
                {'detail': 'Held order not found'},
                status=status.HTTP_404_NOT_FOUND
            )

class POSProductViewSet(ProductServiceMixin, viewsets.ViewSet):
    """POS Product operations - proxy to Product Service"""
    
    @action(detail=False, methods=['GET'])
    def search(self, request):
        """Search products for POS"""
        query = request.query_params.get('q', '')
        if not query:
            return Response(
                {'detail': 'Search query required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        products = self.get_product_data(search=query)
        return Response(products or [])
    
    @action(detail=False, methods=['GET'])
    def categories(self, request):
        """Get POS categories"""
        try:
            base_url = "http://product-service:8000/product_api"
            headers = {
                'X-Profile-ID': request.headers.get('X-Profile-ID'),
                'Authorization': request.headers.get('Authorization')
            }
            
            response = requests.get(f"{base_url}/products/pos_categories/", headers=headers)
            if response.status_code == 200:
                return Response(response.json())
            return Response([])
        except Exception as e:
            logger.error(f"Error fetching categories: {str(e)}")
            return Response([])
    
    @action(detail=False, methods=['GET'])
    def featured(self, request):
        """Get featured products"""
        try:
            base_url = "http://product-service:8000/product_api"
            headers = {
                'X-Profile-ID': request.headers.get('X-Profile-ID'),
                'Authorization': request.headers.get('Authorization')
            }
            
            response = requests.get(f"{base_url}/products/pos_featured/", headers=headers)
            if response.status_code == 200:
                return Response(response.json())
            return Response([])
        except Exception as e:
            logger.error(f"Error fetching featured products: {str(e)}")
            return Response([])
    
    @action(detail=False, methods=['GET'])
    def by_category(self, request):
        """Get products by category"""
        category_id = request.query_params.get('category_id')
        if not category_id:
            return Response(
                {'detail': 'Category ID required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            base_url = "http://product-service:8000/product_api"
            headers = {
                'X-Profile-ID': request.headers.get('X-Profile-ID'),
                'Authorization': request.headers.get('Authorization')
            }
            
            response = requests.get(
                f"{base_url}/products/pos_products/?category={category_id}",
                headers=headers
            )
            if response.status_code == 200:
                return Response(response.json())
            return Response([])
        except Exception as e:
            logger.error(f"Error fetching products by category: {str(e)}")
            return Response([])
    
    @action(detail=True, methods=['GET'])
    def variants(self, request, pk=None):
        """Get product variants"""
        try:
            base_url = "http://product-service:8000/product_api"
            headers = {
                'X-Profile-ID': request.headers.get('X-Profile-ID'),
                'Authorization': request.headers.get('Authorization')
            }
            
            response = requests.get(f"{base_url}/products/{pk}/pos_variants/", headers=headers)
            if response.status_code == 200:
                return Response(response.json())
            return Response([])
        except Exception as e:
            logger.error(f"Error fetching product variants: {str(e)}")
            return Response([])

class POSDiscountViewSet(viewsets.ModelViewSet):
    """POS Discount management"""
    queryset = POSDiscount.objects.all()
    serializer_class = POSDiscountSerializer
    
    def get_queryset(self):
        profile_id = self.request.headers.get('X-Profile-ID')
        return self.queryset.filter(profile=profile_id, is_active=True)

class POSAnalyticsViewSet(viewsets.ViewSet):
    """POS Analytics"""
    
    @action(detail=False, methods=['GET'])
    def daily_sales(self, request):
        """Get daily sales analytics"""
        profile_id = request.headers.get('X-Profile-ID')
        date = request.query_params.get('date', timezone.now().date())
        
        orders = POSOrder.objects.filter(
            profile=profile_id,
            status='completed',
            completed_at__date=date
        )
        
        analytics = {
            'total_orders': orders.count(),
            'total_sales': orders.aggregate(total=Sum('total_amount'))['total'] or 0,
            'average_order_value': orders.aggregate(avg=Avg('total_amount'))['avg'] or 0,
            'total_tax': orders.aggregate(total=Sum('tax_amount'))['total'] or 0,
            'total_discounts': orders.aggregate(total=Sum('discount_amount'))['total'] or 0,
            'payment_methods': orders.values('payments__payment_method').annotate(
                count=Count('payments__payment_method'),
                total=Sum('payments__amount')
            )
        }
        
        return Response(analytics)
