from django.db import models
from django.utils.translation import gettext_lazy as _


class IdentityCompanyProfile(models.Model):
    profile_id = models.BigIntegerField(primary_key=True, verbose_name=_("Profile ID"))
    company_code = models.CharField(max_length=64, unique=True, verbose_name=_("Company Code"))
    display_name = models.CharField(max_length=255, verbose_name=_("Display Name"))
    logo_url = models.URLField(blank=True, default="", max_length=1000, verbose_name=_("Company Logo URL"))
    headquarters_address = models.JSONField(default=dict, blank=True, verbose_name=_("Headquarters Address"))
    headquarters_address_id = models.UUIDField(blank=True, null=True, db_index=True, verbose_name=_("Headquarters Address ID"))
    owner_user_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.display_name


class IdentityUser(models.Model):
    user_id = models.BigIntegerField(primary_key=True, verbose_name=_("User ID"))
    email = models.EmailField(unique=True, verbose_name=_("Email"))
    full_name = models.CharField(max_length=255, blank=True, default="", verbose_name=_("Full Name"))
    is_active = models.BooleanField(default=True, db_index=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["email"]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return self.email


class IdentityMembership(models.Model):
    profile = models.ForeignKey(
        IdentityCompanyProfile,
        on_delete=models.CASCADE,
        related_name="memberships",
        to_field="profile_id",
    )
    user = models.ForeignKey(
        IdentityUser,
        on_delete=models.CASCADE,
        related_name="memberships",
        to_field="user_id",
    )
    role = models.CharField(max_length=50, verbose_name=_("Role"))
    permissions_json = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("profile", "user")
        indexes = [
            models.Index(fields=["profile", "role"]),
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self):
        return f"{self.profile_id}:{self.user_id}"


class IdempotencyRecord(models.Model):
    """Durable replay record for mutating API requests.

    The record is deliberately scoped to a workspace/profile and operation. A
    client retry must either replay the original response or fail with a
    payload conflict; it must never execute the mutation a second time.
    """

    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    profile_id = models.BigIntegerField(db_index=True)
    operation = models.CharField(max_length=160)
    key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, default=STATUS_PROCESSING)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    resource_type = models.CharField(max_length=120, blank=True, default="")
    resource_id = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile_id", "operation", "key"],
                name="unique_idempotency_profile_operation_key",
            ),
        ]
        indexes = [
            models.Index(fields=["profile_id", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.profile_id}:{self.operation}:{self.key}"
