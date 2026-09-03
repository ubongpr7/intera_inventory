from django.db import models


class InventoryBootstrapStatus(models.TextChoices):
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"


class InventoryBootstrap(models.Model):
    """Audit receipt for the paid Inventory workspace bootstrap contract."""

    profile_id = models.BigIntegerField(db_index=True)
    application_slug = models.CharField(max_length=100)
    subscription_id = models.CharField(max_length=100)
    activation_event_id = models.CharField(max_length=150)
    bootstrap_version = models.CharField(max_length=50)
    status = models.CharField(
        max_length=20,
        choices=InventoryBootstrapStatus.choices,
        default=InventoryBootstrapStatus.COMPLETED,
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile_id", "application_slug"],
                name="inventory_bootstrap_profile_application_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["application_slug", "status"],
                name="sb_app_status_idx",
            ),
            models.Index(
                fields=["subscription_id"],
                name="sb_subscription_idx",
            ),
        ]

    def __str__(self):
        return f"{self.profile_id}:{self.application_slug}:{self.status}"
