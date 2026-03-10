from django.db import models
from django.utils.translation import gettext_lazy as _


class IdentityCompanyProfile(models.Model):
    profile_id = models.BigIntegerField(primary_key=True, verbose_name=_("Profile ID"))
    company_code = models.CharField(max_length=64, unique=True, verbose_name=_("Company Code"))
    display_name = models.CharField(max_length=255, verbose_name=_("Display Name"))
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
