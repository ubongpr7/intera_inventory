from rest_framework import serializers
from django.db.models import Sum, Count
from decimal import Decimal

from mainapps.content_type_linking_models.serializers import UserDetailMixin
from mainapps.inventory.models import Inventory

from ..models import (
     StockItem, StockLocation, StockItemTracking
)
from subapps.services.user_service import UserService

class StockLocationListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for location lists"""
    location_type_name = serializers.CharField(source='location_type.name', read_only=True)
    stock_count = serializers.SerializerMethodField()
    
    class Meta:
        model = StockLocation
        fields = ['id', 'name', 'code', 'location_type_name', 'stock_count', 'structural', 'external']
    
    def get_stock_count(self, obj):
        return obj.stock_items.count()

class StockLocationDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Detailed serializer for location CRUD operations"""
    location_type_name = serializers.CharField(source='location_type.name', read_only=True)
    children = StockLocationListSerializer(many=True, read_only=True)
    official_details = serializers.SerializerMethodField()
    stock_summary = serializers.SerializerMethodField()
    
    class Meta:
        model = StockLocation
        fields = '__all__'
        read_only_fields = ['code']
    
    def get_official_details(self, obj):
        return self.get_user_details(obj.official)
    
    def get_stock_summary(self, obj):
        """Get stock summary for this location"""
        stock_items = obj.stock_items.all()
        
        summary = stock_items.aggregate(
            total_items=Count('id'),
            total_quantity=Sum('quantity'),
            total_value=Sum('quantity') * Sum('purchase_price')  # Simplified calculation
        )
        
        # Get top inventory types
        top_types = stock_items.values('inventory__inventory_type').annotate(
            count=Count('id')
        ).order_by('-count')[:5]
        
        summary['top_inventory_types'] = list(top_types)
        return summary

class StockItemListSerializer(serializers.ModelSerializer):
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

class StockInventoryListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for inventory lists"""
    category_name = serializers.CharField(source='category.name', read_only=True)
    current_stock = serializers.SerializerMethodField()
    stock_status = serializers.ReadOnlyField()
    
    class Meta:
        model = Inventory
        fields = [
            'id', 'name', 'IPN', 'external_system_id', 'inventory_type',
            'category_name', 'current_stock', 'stock_status', 'active',
            'minimum_stock_level', 're_order_point', 'created_at'
        ]


class StockItemDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Comprehensive serializer for stock item CRUD operations"""
    inventory_details = StockInventoryListSerializer(source='inventory', read_only=True)
    location_details = StockLocationListSerializer(source='location', read_only=True)
    customer_details = serializers.SerializerMethodField()
    stocktaker_details = serializers.SerializerMethodField()
    
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
        return self.get_user_details(obj.stocktaker)
    
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

class StockItemTrackingListSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Serializer for stock tracking entries"""
    tracking_type_display = serializers.CharField(source='get_tracking_type_display', read_only=True)
    user_details = serializers.SerializerMethodField()
    
    class Meta:
        model = StockItemTracking
        fields = ['id', 'tracking_type', 'tracking_type_display', 'date', 'notes', 'user_details', 'deltas']
    
    def get_user_details(self, obj):
        return self.get_user_details(obj.user)
