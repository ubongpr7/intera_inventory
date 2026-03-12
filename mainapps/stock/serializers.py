from rest_framework import serializers
from django.db.models import Sum, Count
from decimal import Decimal

from mainapps.content_type_linking_models.serializers import UserDetailMixin
from mainapps.inventory.models import Inventory, InventoryItem
from subapps.services.inventory_read_model import (
    get_inventory_item_summary_map,
    get_location_stock_summary,
)

from .models import (
     StockAdjustment, StockItem, StockLocation, StockItemTracking, StockLocationType, StockReservation, StockMovement
)
from subapps.services.catalog_projection import CatalogProjectionLookup

class ProductImageMixin:
    def _resolve_variant_lookup_value(self, obj):
        if isinstance(obj, dict):
            return (
                obj.get('product_variant')
                or obj.get('barcode_snapshot')
                or obj.get('product_variant_id')
                or ''
            )
        for attribute in ('barcode_snapshot',):
            value = getattr(obj, attribute, '')
            if value:
                return value
        product_variant_id = getattr(obj, 'product_variant_id', None)
        return str(product_variant_id) if product_variant_id else ''

    def get_display_image(self, obj):
        request = self.context.get('request')
        variant_lookup = self._resolve_variant_lookup_value(obj)
        if not request or not variant_lookup:
            return None
        try:
            variant_details = CatalogProjectionLookup.get_variant_details_by_barcode(
                barcode=variant_lookup,
                request=request
            )
            
            if variant_details:
                return variant_details.get('image') or variant_details.get('display_image')
        except Exception:
            return None
        return None

class StockLocationTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockLocationType
        fields='__all__'
class StockLocationListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for location lists"""
    location_type_name = serializers.CharField(source='location_type.name', read_only=True)
    stock_count = serializers.SerializerMethodField()
    parent_name=serializers.SerializerMethodField()
    
    class Meta:
        model = StockLocation
        fields = ['id', 'name', 'code','parent_name','location_type_name', 'stock_count', 'structural', 'external','physical_address']
    
    def get_stock_count(self, obj):
        active_balance_count = obj.stock_balances.filter(quantity_on_hand__gt=0).values('inventory_item').distinct().count()
        if active_balance_count:
            return active_balance_count
        return obj.stock_items.count()
    def get_parent_name(self, obj):
        return f'{obj.parent.name} - {obj.parent.code}' if obj.parent else ''

class StockLocationDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Detailed serializer for location CRUD operations"""
    location_type_name = serializers.CharField(source='location_type.name', read_only=True)
    children = StockLocationListSerializer(many=True, read_only=True)
    official_details = serializers.SerializerMethodField()
    stock_summary = serializers.SerializerMethodField()
    parent_name=serializers.SerializerMethodField()
    
    class Meta:
        model = StockLocation
        fields = '__all__'
        read_only_fields = ['code']
    
    def get_official_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'official_user_id', 'official'))
    def get_parent_name(self, obj):
        return f'{obj.parent.name} - {obj.parent.code}' if obj.parent else ''
    
    def get_stock_summary(self, obj):
        """Get stock summary for this location"""
        return get_location_stock_summary(obj)

class InventoryItemSummaryMixin:
    def _get_summary(self, obj):
        summary_map = self.context.get('inventory_item_summary_map') or {}
        return summary_map.get(obj.id) or get_inventory_item_summary_map([obj]).get(obj.id, {})

    def _get_variant_projection(self, obj):
        request = self.context.get('request')
        variant_lookup = getattr(obj, 'barcode_snapshot', '') or (
            str(obj.product_variant_id) if getattr(obj, 'product_variant_id', None) else ''
        )
        if not request or not variant_lookup:
            return None
        return CatalogProjectionLookup.get_variant_details_by_barcode(
            barcode=variant_lookup,
            request=request,
        )


class StockMovementListSerializer(UserDetailMixin, serializers.ModelSerializer):
    movement_type_display = serializers.CharField(source='get_movement_type_display', read_only=True)
    from_location_name = serializers.CharField(source='from_location.name', read_only=True)
    to_location_name = serializers.CharField(source='to_location.name', read_only=True)
    lot_number = serializers.CharField(source='stock_lot.lot_number', read_only=True)
    serial_number = serializers.CharField(source='stock_serial.serial_number', read_only=True)
    actor_details = serializers.SerializerMethodField()

    class Meta:
        model = StockMovement
        fields = [
            'id',
            'movement_type',
            'movement_type_display',
            'quantity',
            'unit_cost',
            'reference_type',
            'reference_id',
            'occurred_at',
            'from_location_name',
            'to_location_name',
            'lot_number',
            'serial_number',
            'notes',
            'actor_details',
        ]

    def get_actor_details(self, obj):
        return self.get_user_details(obj.actor_user_id)


class StockItemListSerializer(ProductImageMixin, InventoryItemSummaryMixin, serializers.ModelSerializer):
    """Compatibility serializer that now exposes InventoryItem summaries."""
    name = serializers.CharField(source='name_snapshot', read_only=True)
    sku = serializers.SerializerMethodField()
    serial = serializers.SerializerMethodField()
    quantity = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    inventory_name = serializers.CharField(source='name_snapshot', read_only=True)
    location_name = serializers.SerializerMethodField()
    expiry_date = serializers.SerializerMethodField()
    days_to_expiry = serializers.SerializerMethodField()
    purchase_price = serializers.SerializerMethodField()
    quantity_w_unit = serializers.SerializerMethodField()
    product_variant = serializers.SerializerMethodField()
    display_image = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            'id', 'name', 'sku', 'serial', 'quantity', 'status',
            'inventory_name', 'location_name', 'expiry_date', 'days_to_expiry',
            'purchase_price', 'created_at','quantity_w_unit','product_variant',
            'display_image'
        ]

    def get_sku(self, obj):
        return self._get_summary(obj).get('sku', obj.sku_snapshot or '')

    def get_serial(self, obj):
        if not obj.track_serial:
            return None
        serial = obj.stock_serials.order_by('created_at').values_list('serial_number', flat=True).first()
        return serial or None

    def get_quantity(self, obj):
        return self._get_summary(obj).get('quantity', Decimal('0'))

    def get_status(self, obj):
        return self._get_summary(obj).get('status', obj.status)

    def get_location_name(self, obj):
        return self._get_summary(obj).get('location_name', '')

    def get_expiry_date(self, obj):
        return self._get_summary(obj).get('expiry_date')

    def get_days_to_expiry(self, obj):
        return self._get_summary(obj).get('days_to_expiry')

    def get_purchase_price(self, obj):
        return self._get_summary(obj).get('purchase_price', Decimal('0'))

    def get_quantity_w_unit(self, obj):
        quantity = self._get_summary(obj).get('quantity', Decimal('0'))
        unit_code = obj.stock_uom_code or obj.default_uom_code or ''
        return f"{quantity} {unit_code}".strip()

    def get_product_variant(self, obj):
        return (
            self._get_summary(obj).get('product_variant')
            or obj.barcode_snapshot
            or (str(obj.product_variant_id) if obj.product_variant_id else '')
        )

class StockInventoryListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for inventory lists"""
    category_name = serializers.CharField(source='category.name', read_only=True)
    current_stock = serializers.SerializerMethodField()
    stock_status = serializers.ReadOnlyField()
    
    class Meta:
        model = Inventory
        fields = [
            'id', 'name', 'external_system_id', 'inventory_type',
            'category_name', 'current_stock', 'stock_status', 'active',
            'minimum_stock_level', 're_order_point', 'created_at'
        ]
      
    def get_current_stock(self, obj):
        return obj.current_stock_level
  


class StockItemDetailSerializer(ProductImageMixin, InventoryItemSummaryMixin, UserDetailMixin, serializers.ModelSerializer):
    """Detailed stock serializer backed by InventoryItem and ledger summaries."""
    name = serializers.CharField(source='name_snapshot', read_only=True)
    sku = serializers.SerializerMethodField()
    product_variant = serializers.SerializerMethodField()
    inventory_name = serializers.CharField(source='name_snapshot', read_only=True)
    quantity = serializers.SerializerMethodField()
    quantity_reserved = serializers.SerializerMethodField()
    quantity_available = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    location_name = serializers.SerializerMethodField()
    location_breakdown = serializers.SerializerMethodField()
    location_count = serializers.SerializerMethodField()
    expiry_date = serializers.SerializerMethodField()
    days_to_expiry = serializers.SerializerMethodField()
    purchase_price = serializers.SerializerMethodField()
    total_stock_value = serializers.SerializerMethodField()
    lot_count = serializers.SerializerMethodField()
    serial_count = serializers.SerializerMethodField()
    created_by_details = serializers.SerializerMethodField()
    updated_by_details = serializers.SerializerMethodField()
    display_image = serializers.SerializerMethodField()
    current_pricing = serializers.SerializerMethodField()
    recent_movements = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            'id',
            'name',
            'description',
            'sku',
            'product_variant',
            'product_template_id',
            'product_variant_id',
            'inventory_type',
            'inventory_category',
            'default_supplier',
            'track_stock',
            'track_lot',
            'track_serial',
            'track_expiry',
            'allow_negative_stock',
            'status',
            'quantity',
            'quantity_reserved',
            'quantity_available',
            'location_name',
            'location_breakdown',
            'location_count',
            'expiry_date',
            'days_to_expiry',
            'purchase_price',
            'total_stock_value',
            'lot_count',
            'serial_count',
            'inventory_name',
            'minimum_stock_level',
            'reorder_point',
            'reorder_quantity',
            'metadata',
            'display_image',
            'current_pricing',
            'recent_movements',
            'created_by_user_id',
            'updated_by_user_id',
            'created_by_details',
            'updated_by_details',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

    def get_sku(self, obj):
        return self._get_summary(obj).get('sku', obj.sku_snapshot or '')

    def get_product_variant(self, obj):
        return (
            self._get_summary(obj).get('product_variant')
            or obj.barcode_snapshot
            or (str(obj.product_variant_id) if obj.product_variant_id else '')
        )

    def get_quantity(self, obj):
        return self._get_summary(obj).get('quantity', Decimal('0'))

    def get_quantity_reserved(self, obj):
        return self._get_summary(obj).get('quantity_reserved', Decimal('0'))

    def get_quantity_available(self, obj):
        return self._get_summary(obj).get('quantity_available', Decimal('0'))

    def get_status(self, obj):
        return self._get_summary(obj).get('status', obj.status)

    def get_location_name(self, obj):
        return self._get_summary(obj).get('location_name', '')

    def get_location_breakdown(self, obj):
        return self._get_summary(obj).get('location_breakdown', [])

    def get_location_count(self, obj):
        return self._get_summary(obj).get('location_count', 0)

    def get_expiry_date(self, obj):
        return self._get_summary(obj).get('expiry_date')

    def get_days_to_expiry(self, obj):
        return self._get_summary(obj).get('days_to_expiry')

    def get_purchase_price(self, obj):
        return self._get_summary(obj).get('purchase_price', Decimal('0'))

    def get_total_stock_value(self, obj):
        return self._get_summary(obj).get('total_stock_value', Decimal('0'))

    def get_lot_count(self, obj):
        return self._get_summary(obj).get('lot_count', 0)

    def get_serial_count(self, obj):
        return self._get_summary(obj).get('serial_count', 0)

    def get_created_by_details(self, obj):
        return self.get_user_details(obj.created_by_user_id)

    def get_updated_by_details(self, obj):
        return self.get_user_details(obj.updated_by_user_id)

    def get_current_pricing(self, obj):
        variant_details = self._get_variant_projection(obj)
        if variant_details:
            return {
                'selling_price': variant_details.get('selling_price'),
                'discount_flat': '0.00',
                'discount_rate': '0.00',
                'tax_rate': variant_details.get('product_details', {}).get('tax_rate', '0.00'),
                'total_price': variant_details.get('selling_price'),
            }
        return None

    def get_recent_movements(self, obj):
        recent = obj.stock_movements.select_related(
            'from_location',
            'to_location',
            'stock_lot',
            'stock_serial',
        )[:5]
        return StockMovementListSerializer(recent, many=True, context=self.context).data


class StockAdjustmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockAdjustment
        fields = '__all__'

class StockItemTrackingListSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Serializer for stock tracking entries"""
    tracking_type_display = serializers.CharField(source='get_tracking_type_display', read_only=True)
    user_details = serializers.SerializerMethodField()
    
    class Meta:
        model = StockItemTracking
        fields = ['id', 'tracking_type', 'tracking_type_display', 'date', 'notes', 'user_details', 'deltas']
    
    def get_user_details(self, obj):
        return UserDetailMixin.get_user_details(
            self,
            self.resolve_user_reference(obj, 'performed_by_user_id', 'user'),
        )

class LowStockItemSerializer(ProductImageMixin, serializers.ModelSerializer):
    inventory_name = serializers.CharField(source='inventory.name', read_only=True)
    minimum_stock_level = serializers.IntegerField(source='inventory.minimum_stock_level', read_only=True)
    re_order_point = serializers.IntegerField(source='inventory.re_order_point', read_only=True)
    shortfall = serializers.SerializerMethodField()
    # display_image = serializers.SerializerMethodField() # Removed from here
    display_image = serializers.SerializerMethodField()
    
    

    class Meta:
        model = StockItem
        fields = [
            'id',
            'name',
            'sku',
            'quantity',
            'inventory_name',
            'minimum_stock_level',
            're_order_point',
            'shortfall',
            'product_variant',
            'display_image' # Added field
        ]

    def get_shortfall(self, obj):
        if obj.inventory and obj.inventory.minimum_stock_level is not None and obj.quantity is not None:
            return obj.inventory.minimum_stock_level - obj.quantity
        return None


class LowStockBalanceSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    sku = serializers.CharField(allow_blank=True)
    quantity = serializers.DecimalField(max_digits=15, decimal_places=5)
    inventory_name = serializers.CharField()
    minimum_stock_level = serializers.DecimalField(max_digits=15, decimal_places=5)
    re_order_point = serializers.DecimalField(max_digits=15, decimal_places=5)
    shortfall = serializers.DecimalField(max_digits=15, decimal_places=5)
    product_variant = serializers.CharField(allow_blank=True)
    display_image = serializers.CharField(allow_null=True, allow_blank=True)


class StockReservationSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='stock_location.name', read_only=True)
    inventory_item_name = serializers.CharField(source='inventory_item.name_snapshot', read_only=True)
    lot_number = serializers.CharField(source='stock_lot.lot_number', read_only=True)
    serial_number = serializers.CharField(source='stock_serial.serial_number', read_only=True)

    class Meta:
        model = StockReservation
        fields = [
            'id',
            'inventory_item',
            'inventory_item_name',
            'stock_lot',
            'lot_number',
            'stock_serial',
            'serial_number',
            'stock_location',
            'location_name',
            'external_order_type',
            'external_order_id',
            'external_order_line_id',
            'reserved_quantity',
            'fulfilled_quantity',
            'status',
            'expires_at',
            'created_at',
            'updated_at',
        ]


class StockReservationCreateSerializer(serializers.Serializer):
    inventory_id = serializers.UUIDField(required=False)
    inventory_item_id = serializers.UUIDField(required=False)
    location_id = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=15, decimal_places=5)
    external_order_type = serializers.CharField(max_length=50)
    external_order_id = serializers.CharField(max_length=100)
    external_order_line_id = serializers.CharField(max_length=100, required=False, allow_blank=True)
    stock_lot_id = serializers.UUIDField(required=False)
    stock_serial_id = serializers.UUIDField(required=False)
    serial_number = serializers.CharField(required=False, allow_blank=True)
    expires_at = serializers.DateTimeField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get('inventory_id') and not attrs.get('inventory_item_id'):
            raise serializers.ValidationError("Either inventory_id or inventory_item_id is required.")
        return attrs


class StockReservationMutationSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=15, decimal_places=5, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    
    
