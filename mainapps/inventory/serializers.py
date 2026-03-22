from decimal import Decimal

from rest_framework import serializers

from mainapps.content_type_linking_models.serializers import UserDetailMixin
from subapps.services.inventory_read_model import get_inventory_item_summary_map

from .models import InventoryCategory, InventoryItem


class InventoryCategoryListSerializer(serializers.ModelSerializer):
    inventory_count = serializers.ReadOnlyField()
    parent_name = serializers.SerializerMethodField()

    class Meta:
        model = InventoryCategory
        fields = ['id', 'name', 'slug', 'is_active', 'inventory_count', 'parent', 'parent_name']

    def get_parent_name(self, obj):
        return obj.parent.name if obj.parent else None


class InventoryCategoryDetailSerializer(UserDetailMixin, serializers.ModelSerializer):
    inventory_count = serializers.ReadOnlyField()
    children = InventoryCategoryListSerializer(many=True, read_only=True)
    created_by_details = serializers.SerializerMethodField()
    modified_by_details = serializers.SerializerMethodField()
    parent_name = serializers.SerializerMethodField()

    class Meta:
        model = InventoryCategory
        fields = '__all__'
        read_only_fields = ['slug', 'created_at', 'modified_at']

    def get_parent_name(self, obj):
        return obj.parent.name if obj.parent else None

    def get_created_by_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'created_by_user_id', 'created_by'))

    def get_modified_by_details(self, obj):
        return self.get_user_details(self.resolve_user_reference(obj, 'updated_by_user_id', 'modified_by'))


class InventoryItemSummaryMixin:
    def _get_summary(self, obj):
        summary_map = self.context.get('inventory_item_summary_map') or {}
        return summary_map.get(obj.id) or get_inventory_item_summary_map([obj]).get(obj.id, {})


class InventoryListSerializer(InventoryItemSummaryMixin, serializers.ModelSerializer):
    name = serializers.CharField(source='name_snapshot', read_only=True)
    category_name = serializers.CharField(source='inventory_category.name', read_only=True)
    stock_status = serializers.SerializerMethodField()
    total_stock_value = serializers.SerializerMethodField()
    current_stock_level = serializers.SerializerMethodField()
    quantity_available = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            'id',
            'name',
            'sku_snapshot',
            'barcode_snapshot',
            'product_variant_id',
            'inventory_type',
            'category_name',
            'stock_status',
            'status',
            'current_stock_level',
            'quantity_available',
            'total_stock_value',
            'minimum_stock_level',
            'reorder_point',
            'reorder_quantity',
            'created_at',
        ]

    def get_stock_status(self, obj):
        return self._get_summary(obj).get('status', obj.status)

    def get_total_stock_value(self, obj):
        return self._get_summary(obj).get('total_stock_value', Decimal('0'))

    def get_current_stock_level(self, obj):
        return self._get_summary(obj).get('quantity', Decimal('0'))

    def get_quantity_available(self, obj):
        return self._get_summary(obj).get('quantity_available', Decimal('0'))


class InventoryDetailSerializer(InventoryItemSummaryMixin, UserDetailMixin, serializers.ModelSerializer):
    name = serializers.CharField(source='name_snapshot', read_only=True)
    category_details = InventoryCategoryListSerializer(source='inventory_category', read_only=True)
    current_stock = serializers.SerializerMethodField()
    stock_status = serializers.SerializerMethodField()
    calculated_safety_stock = serializers.ReadOnlyField(source='safety_stock_level')
    created_by_details = serializers.SerializerMethodField()
    updated_by_details = serializers.SerializerMethodField()
    stock_analytics = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']

    def get_current_stock(self, obj):
        return self._get_summary(obj).get('quantity', Decimal('0'))

    def get_stock_status(self, obj):
        return self._get_summary(obj).get('status', obj.status)

    def get_created_by_details(self, obj):
        return self.get_user_details(obj.created_by_user_id)

    def get_updated_by_details(self, obj):
        return self.get_user_details(obj.updated_by_user_id)

    def get_stock_analytics(self, obj):
        summary = self._get_summary(obj)
        return {
            'total_locations': summary.get('location_count', 0),
            'average_purchase_price': summary.get('avg_purchase_price', Decimal('0')),
            'stock_turnover_rate': 0,
            'days_since_last_movement': None,
            'expiring_soon_count': 1 if summary.get('expiry_date') else 0,
            'quantity_reserved': summary.get('quantity_reserved', Decimal('0')),
            'quantity_available': summary.get('quantity_available', Decimal('0')),
            'location_breakdown': summary.get('location_breakdown', []),
            'lot_count': summary.get('lot_count', 0),
            'serial_count': summary.get('serial_count', 0),
            'last_movement_at': summary.get('last_movement_at'),
        }


class InventoryAnalyticsSerializer(serializers.Serializer):
    total_inventories = serializers.IntegerField()
    active_inventories = serializers.IntegerField()
    low_stock_count = serializers.IntegerField()
    out_of_stock_count = serializers.IntegerField()
    total_stock_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    category_breakdown = serializers.ListField()
    stock_status_distribution = serializers.DictField()
    top_value_items = serializers.ListField()
    expiring_soon = serializers.ListField()


class StockAnalyticsSerializer(serializers.Serializer):
    total_inventory_items = serializers.IntegerField()
    total_locations = serializers.IntegerField()
    total_stock_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    location_distribution = serializers.ListField()
    aging_analysis = serializers.DictField()


class OrderAnalyticsSerializer(serializers.Serializer):
    total_purchase_orders = serializers.IntegerField()
    pending_orders = serializers.IntegerField()
    completed_orders = serializers.IntegerField()
    total_order_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    monthly_trends = serializers.ListField()
    supplier_performance = serializers.ListField()
    status_distribution = serializers.DictField()
