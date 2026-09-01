from decimal import Decimal

from rest_framework import serializers

from mainapps.content_type_linking_models.serializers import UserDetailMixin
from mainapps.projections.models import CatalogVariantProjection
from subapps.services.inventory_read_model import get_inventory_item_summary_map
from subapps.utils.request_context import get_request_profile_id

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
    display_image = serializers.CharField(source='product_variant_image_url', read_only=True)
    stock_status = serializers.SerializerMethodField()
    total_stock_value = serializers.SerializerMethodField()
    current_stock_level = serializers.SerializerMethodField()
    quantity_available = serializers.SerializerMethodField()
    location_name = serializers.SerializerMethodField()
    location_count = serializers.SerializerMethodField()
    location_breakdown = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            'id',
            'name',
            'sku_snapshot',
            'barcode_snapshot',
            'product_variant_id',
            'display_image',
            'product_variant_image_url',
            'inventory_type',
            'category_name',
            'stock_status',
            'status',
            'current_stock_level',
            'quantity_available',
            'total_stock_value',
            'location_name',
            'location_count',
            'location_breakdown',
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

    def get_location_name(self, obj):
        return self._get_summary(obj).get('location_name', '')

    def get_location_count(self, obj):
        return self._get_summary(obj).get('location_count', 0)

    def get_location_breakdown(self, obj):
        return self._get_summary(obj).get('location_breakdown', [])


class InventoryDetailSerializer(InventoryItemSummaryMixin, UserDetailMixin, serializers.ModelSerializer):
    name = serializers.CharField(source='name_snapshot', read_only=True)
    display_image = serializers.CharField(source='product_variant_image_url', read_only=True)
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
        read_only_fields = [
            'profile_id',
            'created_by_user_id',
            'updated_by_user_id',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {
            'name_snapshot': {'required': False},
        }

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

    def validate(self, attrs):
        """Keep manual inventory creation aligned with the catalog projection."""
        variant_id = attrs.get('product_variant_id')
        if variant_id is None:
            if self.instance is None and not attrs.get('name_snapshot'):
                raise serializers.ValidationError(
                    {'name_snapshot': 'Enter a name or select a product variant to create an inventory item.'}
                )
            return attrs

        request = self.context.get('request')
        profile_id = get_request_profile_id(request, as_str=False) if request is not None else None
        if not profile_id:
            raise serializers.ValidationError({'product_variant_id': 'A workspace context is required to link a product variant.'})

        variant = (
            CatalogVariantProjection.objects.select_related('product')
            .filter(
                profile_id=profile_id,
                variant_id=variant_id,
                is_active=True,
                product__is_active=True,
            )
            .first()
        )
        if variant is None:
            raise serializers.ValidationError(
                {'product_variant_id': 'Select an active product variant from this workspace.'}
            )

        duplicate = InventoryItem.objects.filter(
            profile_id=profile_id,
            product_variant_id=variant.variant_id,
        )
        if self.instance is not None:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError({'product_variant_id': 'This product variant already has an inventory item.'})

        attrs.update(
            {
                'product_template_id': variant.product_id,
                'name_snapshot': variant.display_name,
                'sku_snapshot': variant.variant_sku or '',
                'barcode_snapshot': variant.variant_barcode or '',
                'product_variant_image_url': variant.image_url or '',
                'track_stock': True,
            }
        )
        metadata = dict(attrs.get('metadata') or {})
        metadata.update(
            {
                'source': 'catalog_variant_selection',
                'catalog_variant_id': str(variant.variant_id),
                'catalog_product_id': str(variant.product_id),
            }
        )
        attrs['metadata'] = metadata
        return attrs


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


class InventorySetupSummarySerializer(serializers.Serializer):
    total_locations = serializers.IntegerField()
    total_categories = serializers.IntegerField()
    total_inventory_items = serializers.IntegerField()
    total_stock_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    low_stock_count = serializers.IntegerField()


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
