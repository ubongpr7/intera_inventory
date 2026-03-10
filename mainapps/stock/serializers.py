from rest_framework import serializers
from django.db.models import Sum, Count
from decimal import Decimal

from mainapps.content_type_linking_models.serializers import UserDetailMixin
from mainapps.inventory.models import Inventory
from subapps.services.inventory_read_model import get_location_stock_summary

from .models import (
     StockAdjustment, StockItem, StockLocation, StockItemTracking, StockLocationType, StockReservation
)
from subapps.services.catalog_projection import CatalogProjectionLookup

class ProductImageMixin:

    def get_display_image(self, obj):
        request = self.context.get('request')
        if not request or not obj.product_variant:
            return None
        try:

            variant_details = CatalogProjectionLookup.get_variant_details_by_barcode(
                barcode=obj.product_variant,
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

class StockItemListSerializer(ProductImageMixin, serializers.ModelSerializer):
    """Lightweight serializer for stock item lists"""
    inventory_name = serializers.CharField(source='inventory.name', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    days_to_expiry = serializers.SerializerMethodField()
    display_image = serializers.SerializerMethodField()

    
    class Meta:
        model = StockItem
        fields = [
            'id', 'name', 'sku', 'serial', 'quantity', 'status',
            'inventory_name', 'location_name', 'expiry_date', 'days_to_expiry',
            'purchase_price', 'created_at','quantity_w_unit','product_variant',
            'display_image' # Added field
        ]
    def get_days_to_expiry(self, obj):
        if obj.expiry_date:
            from django.utils import timezone
            return (obj.expiry_date - timezone.now().date()).days
        return None

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
  


class StockItemDetailSerializer(ProductImageMixin, UserDetailMixin, serializers.ModelSerializer):
    """Comprehensive serializer for stock item CRUD operations"""
    inventory_details = StockInventoryListSerializer(source='inventory', read_only=True)
    location_details = StockLocationListSerializer(source='location', read_only=True)
    customer_details = serializers.SerializerMethodField()
    stocktaker_details = serializers.SerializerMethodField()
    display_image = serializers.SerializerMethodField()

    
    # Pricing information
    current_pricing = serializers.SerializerMethodField()
    
    # Movement history
    recent_movements = serializers.SerializerMethodField()
    
    class Meta:
        model = StockItem
        fields = '__all__'
        read_only_fields = ['sku', 'serial_int']
    
    def get_customer_details(self, obj):
        return self.get_user_details(obj.customer)
    
    def get_stocktaker_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'stocktaker_user_id', 'stocktaker'))
    
    def get_current_pricing(self, obj):
        """Get current pricing for this stock item"""
        from django.utils import timezone
        
        current_pricing = obj.pricings.filter(
            price_effective_from__lte=timezone.now(),
            price_effective_to__isnull=True
        ).first()
        
        if current_pricing:
            return {
                'selling_price': current_pricing.selling_price,
                'discount_flat': current_pricing.discount_flat,
                'discount_rate': current_pricing.discount_rate,
                'tax_rate': current_pricing.tax_rate,
                'total_price': current_pricing.get_total_price()
            }
        return None
    
    def get_recent_movements(self, obj):
        """Get recent stock movements"""
        recent = obj.tracking_info.all()[:5]
        return StockItemTrackingListSerializer(recent, many=True).data


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

    class Meta:
        model = StockReservation
        fields = [
            'id',
            'inventory_item',
            'inventory_item_name',
            'stock_lot',
            'lot_number',
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
    inventory_id = serializers.UUIDField()
    location_id = serializers.UUIDField()
    quantity = serializers.DecimalField(max_digits=15, decimal_places=5)
    external_order_type = serializers.CharField(max_length=50)
    external_order_id = serializers.CharField(max_length=100)
    external_order_line_id = serializers.CharField(max_length=100, required=False, allow_blank=True)
    stock_lot_id = serializers.UUIDField(required=False)
    expires_at = serializers.DateTimeField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)


class StockReservationMutationSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(max_digits=15, decimal_places=5, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    
    
