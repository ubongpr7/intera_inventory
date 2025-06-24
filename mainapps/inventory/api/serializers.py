from rest_framework import serializers
from django.db.models import Sum, Count, Avg
from decimal import Decimal

from mainapps.content_type_linking_models.serializers import UserDetailMixin
from mainapps.stock.models import StockItem

from ..models import (
    Inventory, InventoryCategory, InventoryBatch, Unit,
)
class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ['id', 'name','dimension_type']

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
        return self.get_user_details(getattr(obj, 'created_by', None))
    
    def get_modified_by_details(self, obj):
        return self.get_user_details(getattr(obj, 'modified_by', None))

class InventoryListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for inventory lists"""
    category_name = serializers.CharField(source='category.name', read_only=True)
    current_stock = serializers.SerializerMethodField()
    stock_status = serializers.ReadOnlyField()
    unit_name=serializers.SerializerMethodField()
    
    class Meta:
        model = Inventory
        fields = [
            'id', 'name', 'external_system_id', 'inventory_type','unit_name',
            'category_name', 'current_stock', 'stock_status', 'active',
            'minimum_stock_level', 're_order_point', 'created_at','re_order_quantity','reorder_strategy'
        ]
    def get_unit_name(self,obj):
        return f'{obj.unit.name} ({obj.unit.dimension_type})'
    def get_current_stock(self, obj):
        return obj.current_stock_level

class InventoryDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    """Comprehensive serializer for inventory CRUD operations"""
    category_details = InventoryCategoryListSerializer(source='category', read_only=True)
    current_stock = serializers.SerializerMethodField()
    total_stock_value = serializers.SerializerMethodField()
    stock_status = serializers.ReadOnlyField()
    calculated_safety_stock = serializers.ReadOnlyField()
    
    # User details
    officer_in_charge_details = serializers.SerializerMethodField()
    created_by_details = serializers.SerializerMethodField()
    modified_by_details = serializers.SerializerMethodField()
    unit_name=serializers.SerializerMethodField()
    
    # Stock analytics
    stock_analytics = serializers.SerializerMethodField()
    
    class Meta:
        model = Inventory
        fields = '__all__'
        read_only_fields = ['external_system_id', 'created_at', 'modified_at']
    
    def get_current_stock(self, obj):
        return obj.current_stock_level
    
    def get_total_stock_value(self, obj):
        return obj.total_stock_value
    
    def get_unit_name(self,obj):
        return obj.get_unit

    def get_officer_in_charge_details(self, obj):
        return self.get_user_details(obj.officer_in_charge)
    
    def get_created_by_details(self, obj):
        return self.get_user_details(getattr(obj, 'created_by', None))
    
    def get_modified_by_details(self, obj):
        return self.get_user_details(getattr(obj, 'modified_by', None))
    
    def get_stock_analytics(self, obj):
        """Get comprehensive stock analytics"""
        stock_items = obj.stock_items.all()
        
        if not stock_items.exists():
            return {
                'total_locations': 0,
                'average_purchase_price': 0,
                'stock_turnover_rate': 0,
                'days_since_last_movement': None,
                'expiring_soon_count': 0
            }
        
        analytics = stock_items.aggregate(
            total_locations=Count('location', distinct=True),
            avg_purchase_price=Avg('purchase_price'),
            total_quantity=Sum('quantity')
        )
        
        from django.utils import timezone
        from datetime import timedelta
        
        expiring_soon = stock_items.filter(
            expiry_date__lte=timezone.now().date() + timedelta(days=30),
            expiry_date__isnull=False
        ).count()
        
        analytics['expiring_soon_count'] = expiring_soon
        analytics['stock_turnover_rate'] = 0  
        analytics['days_since_last_movement'] = None 
        
        return analytics

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
    movement_trends = serializers.DictField()
    
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
