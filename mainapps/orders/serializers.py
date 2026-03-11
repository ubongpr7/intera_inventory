# serializers.py

from rest_framework import serializers
from django.db.models import Count, Sum, Avg, F
from decimal import Decimal
from mainapps.content_type_linking_models.serializers import UserDetailMixin
from mainapps.stock.models import StockItem
from mainapps.inventory.models import Inventory, InventoryItem
from mainapps.orders.models import (
    PurchaseOrder,
    PurchaseOrderLineItem,
    ReturnOrder,
    ReturnOrderLineItem,
    SalesOrder,
    SalesOrderLineItem,
    SalesOrderShipment,
    SalesOrderShipmentLine,
)


class InventoryStockItemListSerializer(UserDetailMixin, serializers.ModelSerializer):
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


class InventoryItemReferenceSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='name_snapshot', read_only=True)
    sku = serializers.CharField(source='sku_snapshot', read_only=True)
    barcode = serializers.CharField(source='barcode_snapshot', read_only=True)
    unit_code = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            'id',
            'name',
            'sku',
            'barcode',
            'product_template_id',
            'product_variant_id',
            'inventory_type',
            'track_stock',
            'track_lot',
            'track_serial',
            'minimum_stock_level',
            'reorder_point',
            'status',
            'unit_code',
        ]

    def get_unit_code(self, obj):
        return obj.stock_uom_code or obj.default_uom_code or ''



class SalesOrderShipmentLineSerializer(serializers.ModelSerializer):
    inventory_name = serializers.CharField(source='sales_order_line.inventory.name', read_only=True)
    location_name = serializers.CharField(source='stock_location.name', read_only=True)
    lot_number = serializers.CharField(source='stock_lot.lot_number', read_only=True)
    serial_number = serializers.CharField(source='stock_serial.serial_number', read_only=True)
    reservation_id = serializers.UUIDField(source='reservation.id', read_only=True)

    class Meta:
        model = SalesOrderShipmentLine
        fields = [
            'id',
            'sales_order_line',
            'inventory_name',
            'stock_location',
            'location_name',
            'stock_lot',
            'lot_number',
            'stock_serial',
            'serial_number',
            'reservation_id',
            'quantity_shipped',
            'notes',
            'created_at',
        ]


class SalesOrderShipmentSerializer(UserDetailMixin, serializers.ModelSerializer):
    lines = SalesOrderShipmentLineSerializer(many=True, read_only=True)
    checked_by_details = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrderShipment
        fields = [
            'id',
            'order',
            'inventory',
            'reference',
            'shipment_date',
            'delivery_date',
            'checked_by',
            'checked_by_user_id',
            'checked_by_details',
            'tracking_number',
            'invoice_number',
            'link',
            'notes',
            'lines',
            'created_at',
            'updated_at',
        ]

    def get_checked_by_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'checked_by_user_id', 'checked_by'))


class SalesOrderLineItemSerializer(serializers.ModelSerializer):
    inventory_name = serializers.CharField(source='inventory.name', read_only=True)
    remaining_quantity = serializers.DecimalField(max_digits=15, decimal_places=5, read_only=True)
    reservable_quantity = serializers.DecimalField(max_digits=15, decimal_places=5, read_only=True)
    total_price = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = SalesOrderLineItem
        fields = [
            'id',
            'sales_order',
            'inventory',
            'inventory_item',
            'inventory_name',
            'quantity',
            'reserved_quantity',
            'shipped_quantity',
            'remaining_quantity',
            'reservable_quantity',
            'unit_price',
            'discount_rate',
            'tax_rate',
            'description',
            'total_price',
            'created_at',
            'updated_at',
        ]


class SalesOrderListSerializer(UserDetailMixin, serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    line_items_count = serializers.SerializerMethodField()
    total_price = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrder
        fields = [
            'id',
            'reference',
            'status',
            'customer',
            'customer_name',
            'shipment_date',
            'issue_date',
            'delivery_date',
            'line_items_count',
            'total_price',
            'created_at',
            'updated_at',
        ]

    def get_line_items_count(self, obj):
        return obj.line_items.count()

    def get_total_price(self, obj):
        return str(obj.total_price.quantize(Decimal('0.00'))) if obj.total_price else "0.00"


class SalesOrderDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    line_items = SalesOrderLineItemSerializer(many=True, read_only=True)
    shipments = SalesOrderShipmentSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    responsible_details = serializers.SerializerMethodField()
    shipped_by_details = serializers.SerializerMethodField()
    total_price = serializers.SerializerMethodField()

    class Meta:
        model = SalesOrder
        fields = '__all__'
        read_only_fields = ['reference']

    def get_responsible_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'responsible_user_id', 'responsible'))

    def get_shipped_by_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'shipped_by_user_id', 'shipped_by'))

    def get_total_price(self, obj):
        return str(obj.total_price.quantize(Decimal('0.00'))) if obj.total_price else "0.00"


class SalesOrderLineItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesOrderLineItem
        exclude = ['sales_order']
        read_only_fields = ['reserved_quantity', 'shipped_quantity', 'total_price']


class SalesOrderReserveSerializer(serializers.Serializer):
    reservation_items = serializers.ListField(
        child=serializers.DictField(),
        help_text="List of sales order line items to reserve against stock",
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_reservation_items(self, value):
        required_fields = ['line_item_id', 'location_id']
        for item in value:
            for field in required_fields:
                if field not in item:
                    raise serializers.ValidationError(
                        f"Missing required field '{field}' in reservation items"
                    )
            if 'quantity' in item and item['quantity'] <= 0:
                raise serializers.ValidationError("Reservation quantity must be greater than zero")
        return value


class SalesOrderReleaseSerializer(serializers.Serializer):
    reservation_items = serializers.ListField(
        child=serializers.DictField(),
        help_text="List of reservation records to release",
    )
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_reservation_items(self, value):
        for item in value:
            if 'reservation_id' not in item:
                raise serializers.ValidationError("Missing required field 'reservation_id'")
            if 'quantity' in item and item['quantity'] <= 0:
                raise serializers.ValidationError("Release quantity must be greater than zero")
        return value


class SalesOrderShipSerializer(serializers.Serializer):
    shipment_items = serializers.ListField(
        child=serializers.DictField(),
        help_text="List of sales order line items to ship",
    )
    shipment_date = serializers.DateField(required=False, allow_null=True)
    delivery_date = serializers.DateField(required=False, allow_null=True)
    tracking_number = serializers.CharField(required=False, allow_blank=True)
    invoice_number = serializers.CharField(required=False, allow_blank=True)
    link = serializers.URLField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_shipment_items(self, value):
        for item in value:
            if 'reservation_id' not in item and 'line_item_id' not in item:
                raise serializers.ValidationError(
                    "Each shipment item must include either 'reservation_id' or 'line_item_id'"
                )
            if 'reservation_id' not in item and 'location_id' not in item:
                raise serializers.ValidationError(
                    "Shipment items without a reservation_id must include 'location_id'"
                )
            if 'quantity' in item and item['quantity'] <= 0:
                raise serializers.ValidationError("Shipment quantity must be greater than zero")
        return value

class PurchaseOrderLineItemSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Serializer for purchase order line items"""
    inventory_item_name = serializers.CharField(source='inventory_item.name_snapshot', read_only=True)
    inventory_item_details = InventoryItemReferenceSerializer(source='inventory_item', read_only=True)
    stock_item_details = InventoryStockItemListSerializer(source='stock_item', read_only=True)
    quantity_w_unit = serializers.SerializerMethodField()
    tax_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total_price = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    def get_quantity_w_unit(self, obj):
        unit = ''
        if obj.inventory_item:
            unit = obj.inventory_item.stock_uom_code or obj.inventory_item.default_uom_code or ''
        elif obj.stock_item and obj.stock_item.inventory:
            unit = obj.stock_item.inventory.unit or ''
        return f"{obj.quantity} {unit}"

    class Meta:
        model = PurchaseOrderLineItem
        fields = [
            'id', 'purchase_order', 'inventory_item', 'inventory_item_name', 'inventory_item_details',
            'stock_item', 'stock_item_details',
            'quantity', 'quantity_w_unit', 'unit_price',
            'discount_rate', 'tax_rate', 'description',
            'batch_number', 'expiry_date', 'manufactured_date', 'quantity_received',
            'fully_received', 'tax_amount', 'discount', 'total_price'
        ]
        read_only_fields = ['tax_amount', 'discount', 'total_price']


class PurchaseOrderListSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Lightweight serializer for purchase order lists"""
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    line_items_count = serializers.SerializerMethodField()
    total_price = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'reference', 'status', 'supplier_name',
            'total_price', 'issue_date', 'delivery_date',
            'line_items_count', 'created_at', 'workflow_state'
        ]

    def get_line_items_count(self, obj):
        return obj.line_items.count()

    def get_total_price(self, obj):
        # obj.total_price is a @property
        return str(obj.total_price.quantize(Decimal('0.00'))) if obj.total_price else "0.00"


class PurchaseOrderDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Comprehensive serializer for purchase order CRUD operations"""
    line_items = PurchaseOrderLineItemSerializer(many=True, read_only=True)
    supplier_details = serializers.SerializerMethodField()
    responsible_details = serializers.SerializerMethodField()
    received_by_details = serializers.SerializerMethodField()
    total_price = serializers.SerializerMethodField()
    order_analytics = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = '__all__'
        read_only_fields = ['reference', 'total_price']

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

    def get_total_price(self, obj):
        return str(obj.total_price.quantize(Decimal('0.00'))) if obj.total_price else "0.00"

    def get_order_analytics(self, obj):
        """Get order analytics"""
        line_items = obj.line_items.all()

        if not line_items.exists():
            return {
                'total_items': 0,
                'total_quantity': 0,
                'average_unit_price': "0.00",
                'total_discount': "0.00",
                'total_tax': "0.00"
            }

        total_quantity = sum(item.quantity for item in line_items)
        total_discount = sum(item.discount for item in line_items)
        total_tax = sum(item.tax_amount for item in line_items)
        avg_unit_price = (
            sum(item.unit_price for item in line_items) / line_items.count()
            if line_items.count() > 0 else Decimal('0.00')
        )

        return {
            'total_items': line_items.count(),
            'total_quantity': total_quantity,
            'average_unit_price': str(avg_unit_price.quantize(Decimal('0.00'))),
            'total_discount': str(total_discount.quantize(Decimal('0.00'))),
            'total_tax': str(total_tax.quantize(Decimal('0.00')))
        }


class PurchaseOrderLineItemCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating purchase order line items"""
    purchase_order_reference = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = PurchaseOrderLineItem
        exclude = ['purchase_order']
        read_only_fields = ['tax_amount', 'discount', 'total_price', 'batch_number']

    def create(self, validated_data):
        po_reference = validated_data.pop('purchase_order_reference', None)
        if po_reference:
            try:
                purchase_order = PurchaseOrder.objects.get(reference=po_reference)
                validated_data['purchase_order'] = purchase_order
            except PurchaseOrder.DoesNotExist:
                raise serializers.ValidationError({
                    'purchase_order_reference': 'Purchase order not found'
                })
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


class ReturnOrderLineItemSerializer(UserDetailMixin, serializers.ModelSerializer):
    original_line_item_id = serializers.UUIDField(source='original_line_item.id', read_only=True)
    inventory_item_name = serializers.SerializerMethodField()
    remaining_quantity = serializers.DecimalField(max_digits=15, decimal_places=5, read_only=True)

    class Meta:
        model = ReturnOrderLineItem
        fields = [
            'id',
            'original_line_item',
            'original_line_item_id',
            'inventory_item_name',
            'quantity_returned',
            'quantity_processed',
            'remaining_quantity',
            'return_reason',
            'unit_price',
            'tax_rate',
            'discount',
            'created_at',
            'updated_at',
        ]

    def get_inventory_item_name(self, obj):
        if obj.original_line_item.inventory_item:
            return obj.original_line_item.inventory_item.name_snapshot
        if obj.original_line_item.stock_item:
            return obj.original_line_item.stock_item.name
        return ""


class ReturnOrderListSerializer(serializers.ModelSerializer):
    purchase_order_reference = serializers.CharField(source='purchase_order.reference', read_only=True)
    supplier_name = serializers.CharField(source='purchase_order.supplier.name', read_only=True)
    line_items_count = serializers.SerializerMethodField()
    total_price = serializers.SerializerMethodField()

    class Meta:
        model = ReturnOrder
        fields = [
            'id',
            'reference',
            'status',
            'purchase_order_reference',
            'supplier_name',
            'line_items_count',
            'total_price',
            'issue_date',
            'complete_date',
            'created_at',
        ]

    def get_line_items_count(self, obj):
        return obj.line_items.count()

    def get_total_price(self, obj):
        return str(sum((line.total_price for line in obj.line_items.all()), Decimal('0.00')))


class ReturnOrderDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    line_items = ReturnOrderLineItemSerializer(many=True, read_only=True)
    purchase_order_reference = serializers.CharField(source='purchase_order.reference', read_only=True)
    supplier_name = serializers.CharField(source='purchase_order.supplier.name', read_only=True)
    responsible_details = serializers.SerializerMethodField()

    class Meta:
        model = ReturnOrder
        fields = '__all__'

    def get_responsible_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'responsible_user_id', 'responsible'))


class ReturnOrderProcessSerializer(serializers.Serializer):
    return_items = serializers.ListField(
        child=serializers.DictField(),
        help_text="List of return line items to issue out of stock",
    )
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_return_items(self, value):
        required_fields = ['return_line_item_id', 'location_id']
        for item in value:
            for field in required_fields:
                if field not in item:
                    raise serializers.ValidationError(
                        f"Missing required field '{field}' in return items"
                    )
            if 'quantity' in item and item['quantity'] <= 0:
                raise serializers.ValidationError("Quantity must be greater than zero")
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

    total_order_value = serializers.DecimalField(max_digits=18, decimal_places=2)
    average_order_value = serializers.DecimalField(max_digits=18, decimal_places=2)

    monthly_trends = serializers.ListField(child=serializers.DictField())
    weekly_trends = serializers.ListField(child=serializers.DictField())

    supplier_performance = serializers.ListField(child=serializers.DictField())
    top_suppliers_by_value = serializers.ListField(child=serializers.DictField())

    status_distribution = serializers.DictField()
    average_processing_time = serializers.FloatField()
    average_delivery_time = serializers.FloatField()
    on_time_delivery_rate = serializers.FloatField()

    total_savings = serializers.DecimalField(max_digits=18, decimal_places=2)
    cost_per_order = serializers.DecimalField(max_digits=18, decimal_places=2)
