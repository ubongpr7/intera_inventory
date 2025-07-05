# mainapps/pos/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import POSOrder, POSOrderItem

@receiver(post_save, sender=POSOrderItem)
def update_order_totals(sender, instance, created, **kwargs):
    """Update order totals when an order item is created or updated"""
    instance.order.calculate_totals()


# # pos/signals.py
# @receiver(post_save, sender=POSOrder)
# def sync_inventory(sender, instance, **kwargs):
#     if instance.status == 'finalized' and not instance.inventory_adjusted:
#         inventory_adjustment.delay(instance.id)

# @receiver(post_save, sender=IntegratedPayment)
# def sync_accounting(sender, instance, **kwargs):
#     if not instance.synced_with_accounting:
#         accounting_sync.delay(instance.id)