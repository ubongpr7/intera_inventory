from rest_framework import serializers
from django.db.models import Sum, Count, Avg
from decimal import Decimal

from mainapps.content_type_linking_models.serializers import UserDetailMixin
from mainapps.stock.models import StockItem
from subapps.services.inventory_read_model import get_inventory_summary_map

from .models import (
    Inventory, InventoryCategory, InventoryBatch
)

class InventoryCategoryListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for category lists"""
    inventory_count = serializers.ReadOnlyField()
    parent_name= serializers.SerializerMethodField()
    
    class Meta:
        model = InventoryCategory
        fields = ['id', 'name', 'slug', 'is_active', 'inventory_count', 'parent','parent_name']
    def get_parent_name(self,obj):
        return obj.parent.name if obj.parent else None
    
class InventoryCategoryDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Detailed serializer for category CRUD operations"""
    inventory_count = serializers.ReadOnlyField()
    children = InventoryCategoryListSerializer(many=True, read_only=True)
    created_by_details = serializers.SerializerMethodField()
    modified_by_details = serializers.SerializerMethodField()
    parent_name= serializers.SerializerMethodField()
    
    class Meta:
        model = InventoryCategory
        fields = '__all__'
        read_only_fields = ['slug', 'created_at', 'modified_at']
    def get_parent_name(self,obj):
        return obj.parent.name if obj.parent else None
    
    def get_created_by_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'created_by_user_id', 'created_by'))
    
    def get_modified_by_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'updated_by_user_id', 'modified_by'))

class InventoryListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for inventory lists"""
    category_name = serializers.CharField(source='category.name', read_only=True)
    stock_status = serializers.SerializerMethodField()
    total_stock_value = serializers.SerializerMethodField()
    current_stock_level = serializers.SerializerMethodField()
    
    class Meta:
        model = Inventory
        fields = [
            'id', 'name', 'external_system_id', 'inventory_type','unit_name','total_stock_value',
            'category_name', 'stock_status', 'active','current_stock_level',
            'minimum_stock_level', 're_order_point', 'created_at','re_order_quantity','reorder_strategy'
        ]

    def _get_summary(self, obj):
        summary_map = self.context.get('inventory_summary_map') or {}
        return summary_map.get(obj.id) or get_inventory_summary_map([obj]).get(obj.id, {})

    def get_stock_status(self, obj):
        return self._get_summary(obj).get('stock_status', obj.stock_status)

    def get_total_stock_value(self, obj):
        return self._get_summary(obj).get('total_stock_value', obj.total_stock_value)

    def get_current_stock_level(self, obj):
        return self._get_summary(obj).get('current_stock_level', obj.current_stock_level)

class InventoryDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Comprehensive serializer for inventory CRUD operations"""
    category_details = InventoryCategoryListSerializer(source='category', read_only=True)
    current_stock = serializers.SerializerMethodField()
    stock_status = serializers.SerializerMethodField()
    calculated_safety_stock = serializers.ReadOnlyField()
    
    # User details
    officer_in_charge_details = serializers.SerializerMethodField()
    created_by_details = serializers.SerializerMethodField()
    modified_by_details = serializers.SerializerMethodField()
    
    # Stock analytics
    stock_analytics = serializers.SerializerMethodField()
    
    class Meta:
        model = Inventory
        fields = '__all__'
        read_only_fields = ['external_system_id', 'created_at', 'modified_at']
    
    def _get_summary(self, obj):
        summary_map = self.context.get('inventory_summary_map') or {}
        return summary_map.get(obj.id) or get_inventory_summary_map([obj]).get(obj.id, {})

    def get_current_stock(self, obj):
        return self._get_summary(obj).get('current_stock_level', obj.current_stock_level)

    def get_stock_status(self, obj):
        return self._get_summary(obj).get('stock_status', obj.stock_status)
    

    def get_officer_in_charge_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'officer_in_charge_user_id', 'officer_in_charge'))
    
    def get_created_by_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'created_by_user_id', 'created_by'))
    
    def get_modified_by_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'updated_by_user_id', 'modified_by'))
    
    def get_stock_analytics(self, obj):
        """Get comprehensive stock analytics"""
        summary = self._get_summary(obj)
        return {
            'total_locations': summary.get('total_locations', 0),
            'average_purchase_price': summary.get('avg_purchase_price', Decimal('0')),
            'stock_turnover_rate': 0,
            'days_since_last_movement': None,
            'expiring_soon_count': summary.get('expiring_soon_count', 0),
            'quantity_reserved': summary.get('quantity_reserved', Decimal('0')),
            'quantity_available': summary.get('quantity_available', Decimal('0')),
            'location_breakdown': summary.get('location_breakdown', []),
        }

# Analytics Serializers
class InventoryAnalyticsSerializer(serializers.Serializer):
    """Serializer for inventory analytics data"""
    total_inventories = serializers.IntegerField()
    active_inventories = serializers.IntegerField()
    low_stock_count = serializers.IntegerField()
    out_of_stock_count = serializers.IntegerField()
    total_stock_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    
    # Category breakdown
    category_breakdown = serializers.ListField()
    
    # Stock status distribution
    stock_status_distribution = serializers.DictField()
    
    # Top performing items
    top_value_items = serializers.ListField()
    
    # Expiry alerts
    expiring_soon = serializers.ListField()

class StockAnalyticsSerializer(serializers.Serializer):
    """Serializer for stock analytics data"""
    total_stock_items = serializers.IntegerField()
    total_locations = serializers.IntegerField()
    total_stock_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    
    # Location distribution
    location_distribution = serializers.ListField()
    
    # Stock movement trends
    # movement_trends = serializers.DictField()
    
    # Aging analysis
    aging_analysis = serializers.DictField()

class OrderAnalyticsSerializer(serializers.Serializer):
    """Serializer for order analytics data"""
    total_purchase_orders = serializers.IntegerField()
    pending_orders = serializers.IntegerField()
    completed_orders = serializers.IntegerField()
    total_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    
    # Monthly trends
    monthly_trends = serializers.ListField()
    
    # Supplier performance
    supplier_performance = serializers.ListField()
    
    # Order status distribution
    status_distribution = serializers.DictField()
