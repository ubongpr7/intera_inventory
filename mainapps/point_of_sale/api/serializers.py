from rest_framework import serializers
from decimal import Decimal
from ..models import (
    POSConfiguration, POSTerminal, Customer, Table, POSSession,
    POSOrder, POSOrderItem, POSPayment, POSDiscount, POSReceipt, POSHoldOrder
)

class POSConfigurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSConfiguration
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'profile']

class POSTerminalSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSTerminal
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'profile']

class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'profile']

class TableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Table
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'profile']

class POSSessionSerializer(serializers.ModelSerializer):
    total_sales = serializers.ReadOnlyField()
    
    class Meta:
        model = POSSession
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'profile', 'total_sales']

class POSOrderItemSerializer(serializers.ModelSerializer):
    customization_cost = serializers.SerializerMethodField()
    
    class Meta:
        model = POSOrderItem
        fields = '__all__'
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'created_by', 'profile',
            'line_subtotal', 'discount_amount', 'tax_amount', 'line_total'
        ]
    
    def get_customization_cost(self, obj):
        return float(obj.calculate_customization_cost())

class POSPaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSPayment
        fields = '__all__'
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'created_by', 'profile',
            'change_given', 'is_processed', 'processed_at'
        ]

class POSOrderSerializer(serializers.ModelSerializer):
    items = POSOrderItemSerializer(many=True, read_only=True)
    payments = POSPaymentSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    table_number = serializers.CharField(source='table.number', read_only=True)
    total_paid = serializers.SerializerMethodField()
    remaining_balance = serializers.SerializerMethodField()
    
    class Meta:
        model = POSOrder
        fields = '__all__'
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'created_by', 'profile',
            'order_number', 'subtotal', 'tax_amount', 'total_amount',
            'completed_at', 'items', 'payments', 'customer_name', 'table_number',
            'total_paid', 'remaining_balance'
        ]
    
    def get_total_paid(self, obj):
        return float(obj.payments.filter(is_processed=True).aggregate(
            total=serializers.models.Sum('amount')
        )['total'] or 0)
    
    def get_remaining_balance(self, obj):
        total_paid = self.get_total_paid(obj)
        return float(obj.total_amount - Decimal(str(total_paid)))

class POSDiscountSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSDiscount
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by', 'profile']

class POSReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSReceipt
        fields = '__all__'
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'created_by', 'profile',
            'receipt_number'
        ]

class POSHoldOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSHoldOrder
        fields = '__all__'
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'created_by', 'profile',
            'retrieved_at', 'retrieved_by'
        ]
