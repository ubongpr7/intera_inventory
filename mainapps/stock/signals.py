from django.db.models.signals import post_delete
from django.dispatch import receiver

from mainapps.stock.models import StockLocation, ensure_single_default_structural_location


@receiver(post_delete, sender=StockLocation)
def restore_workspace_default_structural_location(sender, instance, **kwargs):
    if instance.profile_id is None or not instance.structural:
        return
    ensure_single_default_structural_location(profile_id=instance.profile_id)
