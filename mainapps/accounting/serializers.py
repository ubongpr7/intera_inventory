from rest_framework import serializers

from .models import InventoryValuationSnapshot, JournalEntry, JournalLine


class JournalLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = JournalLine
        fields = ['id', 'account_code', 'debit', 'credit', 'memo']


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True, read_only=True)

    class Meta:
        model = JournalEntry
        fields = ['id', 'entry_type', 'reference_type', 'reference_id', 'entry_date', 'currency_code', 'memo', 'lines', 'created_at']


class InventoryValuationSnapshotSerializer(serializers.ModelSerializer):
    inventory_item_name = serializers.CharField(source='inventory_item.name_snapshot', read_only=True)
    stock_location_name = serializers.CharField(source='stock_location.name', read_only=True)

    class Meta:
        model = InventoryValuationSnapshot
        fields = [
            'id', 'snapshot_date', 'inventory_item', 'inventory_item_name',
            'stock_location', 'stock_location_name', 'costing_method',
            'quantity_on_hand', 'unit_cost', 'total_value', 'currency_code', 'created_at',
        ]
