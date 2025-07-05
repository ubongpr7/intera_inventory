# mainapps/pos/admin.py
from django.contrib import admin
from .models import (
    POSSession, POSOrder, POSOrderItem, POSPayment, 
    POSDiscount, POSTable, POSModifierGroup, POSModifier,
    POSOrderItemModifier
)

class POSOrderItemInline(admin.TabularInline):
    model = POSOrderItem
    extra = 0

class POSPaymentInline(admin.TabularInline):
    model = POSPayment
    extra = 0

@admin.register(POSSession)
class POSSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'opening_time', 'closing_time', 'is_active')
    list_filter = ('is_active', 'user')
    search_fields = ('user__username',)

@admin.register(POSOrder)
class POSOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'session', 'status', 'created_at', 'total_amount', 'payment_status')
    list_filter = ('status', 'payment_status', 'payment_method')
    search_fields = ('order_number', 'customer_name', 'customer_phone')
    inlines = [POSOrderItemInline, POSPaymentInline]
    readonly_fields = ('order_number', 'created_at', 'updated_at')

@admin.register(POSOrderItem)
class POSOrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'quantity', 'unit_price', 'line_total')
    list_filter = ('order__status',)
    search_fields = ('order__order_number', 'product__name')

@admin.register(POSPayment)
class POSPaymentAdmin(admin.ModelAdmin):
    list_display = ('order', 'payment_method', 'amount', 'payment_date')
    list_filter = ('payment_method', 'payment_date')
    search_fields = ('order__order_number', 'reference_number')

@admin.register(POSDiscount)
class POSDiscountAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'amount', 'percentage', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')

@admin.register(POSTable)
class POSTableAdmin(admin.ModelAdmin):
    list_display = ('name', 'capacity', 'status', 'section')
    list_filter = ('status', 'section')
    search_fields = ('name',)

@admin.register(POSModifierGroup)
class POSModifierGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'required', 'multi_select', 'min_selections', 'max_selections')
    search_fields = ('name',)

@admin.register(POSModifier)
class POSModifierAdmin(admin.ModelAdmin):
    list_display = ('name', 'group', 'price')
    list_filter = ('group',)
    search_fields = ('name',)

@admin.register(POSOrderItemModifier)
class POSOrderItemModifierAdmin(admin.ModelAdmin):
    list_display = ('order_item', 'modifier', 'price')
    search_fields = ('order_item__product__name', 'modifier__name')