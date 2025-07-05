from rest_framework import serializers
from django.db.models import Sum, Count, Avg
from decimal import Decimal
from mainapps.content_type_linking_models.serializers import UserDetailMixin
from mainapps.stock.models import StockItem, StockLocation, StockItemTracking
from mainapps.inventory.models import Inventory
from mainapps.orders.models import PurchaseOrder, PurchaseOrderLineItem

class InventoryStockItemListSerializer(UserDetailMixin,serializers.ModelSerializer):
    """Lightweight serializer for stock item lists"""
    inventory_name = serializers.CharField(source='inventory.name', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    days_to_expiry = serializers.SerializerMethodField()
    
    class Meta:
        model = StockItem
        fields = [
            'id', 'name', 'sku', 'serial', 'quantity', 'status',
            'inventory_name', 'location_name', 'expiry_date', 'days_to_expiry',
            'purchase_price', 'created_at'
        ]
    
    def get_days_to_expiry(self, obj):
        if obj.expiry_date:
            from django.utils import timezone
            return (obj.expiry_date - timezone.now().date()).days
        return None

class PurchaseOrderLineItemSerializer(UserDetailMixin,serializers.ModelSerializer):
    """Serializer for purchase order line items"""
    stock_item_details = InventoryStockItemListSerializer(source='stock_item', read_only=True)
    
    class Meta:
        model = PurchaseOrderLineItem
        fields = '__all__'
        read_only_fields = ['total_price']

class PurchaseOrderListSerializer(UserDetailMixin,serializers.ModelSerializer):
    """Lightweight serializer for purchase order lists"""
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    line_items_count = serializers.SerializerMethodField()
    
    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'reference', 'status', 'supplier_name', 'total_price',
            'issue_date', 'delivery_date', 'line_items_count', 'created_at'
        ]
    
    def get_line_items_count(self, obj):
        return obj.line_items.count()

class PurchaseOrderDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Comprehensive serializer for purchase order CRUD operations"""
    line_items = PurchaseOrderLineItemSerializer(many=True, read_only=True)
    supplier_details = serializers.SerializerMethodField()
    responsible_details = serializers.SerializerMethodField()
    received_by_details = serializers.SerializerMethodField()
    
    # Order analytics
    order_analytics = serializers.SerializerMethodField()
    
    class Meta:
        model = PurchaseOrder
        fields = '__all__'
        read_only_fields = ['reference']
    
    def get_supplier_details(self, obj):
        if obj.supplier:
            return {
                'id': obj.supplier.id,
                'name': obj.supplier.name,
                'email': getattr(obj.supplier, 'email', ''),
                'phone': getattr(obj.supplier, 'phone', ''),
            }
        return None
    
    def get_responsible_details(self, obj):
        return self.get_user_details(obj.responsible)
    
    def get_received_by_details(self, obj):
        return self.get_user_details(obj.received_by)
    
    def get_order_analytics(self, obj):
        """Get order analytics"""
        line_items = obj.line_items.all()
        
        if not line_items.exists():
            return {
                'total_items': 0,
                'total_quantity': 0,
                'average_unit_price': 0,
                'total_discount': 0,
                'total_tax': 0
            }
        
        analytics = line_items.aggregate(
            total_items=Count('id'),
            total_quantity=Sum('quantity'),
            avg_unit_price=Avg('unit_price'),
            total_discount=Sum('discount'),
            total_tax=Sum('tax_amount')
        )
        
        return analytics



class PurchaseOrderLineItemCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating purchase order line items"""
    purchase_order_reference = serializers.CharField(write_only=True, required=False)
    
    class Meta:
        model = PurchaseOrderLineItem
        fields = '__all__'
        read_only_fields = ['total_price', 'purchase_order']
    
    def create(self, validated_data):
        # Handle purchase order reference
        po_reference = validated_data.pop('purchase_order_reference', None)
        if po_reference:
            try:
                purchase_order = PurchaseOrder.objects.get(reference=po_reference)
                validated_data['purchase_order'] = purchase_order
            except PurchaseOrder.DoesNotExist:
                raise serializers.ValidationError(
                    {'purchase_order_reference': 'Purchase order not found'}
                )
        
        return super().create(validated_data)

class PurchaseOrderWorkflowSerializer(serializers.Serializer):
    """Serializer for purchase order workflow actions"""
    notes = serializers.CharField(required=False, allow_blank=True)
    notify_supplier = serializers.BooleanField(default=True)
    
class ReceiveItemsSerializer(serializers.Serializer):
    """Serializer for receiving purchase order items"""
    received_items = serializers.ListField(
        child=serializers.DictField(),
        help_text="List of items being received"
    )
    
    def validate_received_items(self, value):
        """Validate received items structure"""
        required_fields = ['line_item_id', 'quantity_received', 'location_id']
        
        for item in value:
            for field in required_fields:
                if field not in item:
                    raise serializers.ValidationError(
                        f"Missing required field '{field}' in received items"
                    )
            
            if item['quantity_received'] <= 0:
                raise serializers.ValidationError(
                    "Quantity received must be greater than 0"
                )
        
        return value

class ReturnOrderCreateSerializer(serializers.Serializer):
    """Serializer for creating return orders"""
    return_items = serializers.ListField(
        child=serializers.DictField(),
        help_text="List of items to return"
    )
    return_reason = serializers.CharField(required=False, allow_blank=True)
    
    def validate_return_items(self, value):
        """Validate return items structure"""
        required_fields = ['line_item_id', 'quantity', 'reason']
        
        for item in value:
            for field in required_fields:
                if field not in item:
                    raise serializers.ValidationError(
                        f"Missing required field '{field}' in return items"
                    )
            
            if item['quantity'] <= 0:
                raise serializers.ValidationError(
                    "Return quantity must be greater than 0"
                )
        
        return value

class PurchaseOrderAnalyticsSerializer(serializers.Serializer):
    """Enhanced analytics serializer"""
    total_purchase_orders = serializers.IntegerField()
    pending_orders = serializers.IntegerField()
    approved_orders = serializers.IntegerField()
    issued_orders = serializers.IntegerField()
    received_orders = serializers.IntegerField()
    completed_orders = serializers.IntegerField()
    cancelled_orders = serializers.IntegerField()
    
    total_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    average_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    
    # Time-based analytics
    monthly_trends = serializers.ListField()
    weekly_trends = serializers.ListField()
    
    # Supplier analytics
    supplier_performance = serializers.ListField()
    top_suppliers_by_value = serializers.ListField()
    
    # Status distribution
    status_distribution = serializers.DictField()
    
    # Performance metrics
    average_processing_time = serializers.FloatField()
    average_delivery_time = serializers.FloatField()
    on_time_delivery_rate = serializers.FloatField()
    
    # Financial metrics
    total_savings = serializers.DecimalField(max_digits=15, decimal_places=2)
    cost_per_order = serializers.DecimalField(max_digits=15, decimal_places=2)
