import uuid

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class CatalogProductProjection(models.Model):
    product_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("Product ID"))
    profile_id = models.BigIntegerField(db_index=True, verbose_name=_("Profile ID"))
    name = models.CharField(max_length=255, verbose_name=_("Product Name"))
    category_name = models.CharField(max_length=255, blank=True, default="")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    track_stock = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["profile_id", "is_active"]),
        ]

    def __str__(self):
        return self.name


class CatalogVariantProjection(models.Model):
    variant_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name=_("Variant ID"))
    product = models.ForeignKey(
        CatalogProductProjection,
        on_delete=models.CASCADE,
        related_name="variants",
        to_field="product_id",
    )
    profile_id = models.BigIntegerField(db_index=True, verbose_name=_("Profile ID"))
    display_name = models.CharField(max_length=255, verbose_name=_("Display Name"))
    variant_name = models.CharField(max_length=255, blank=True, default="")
    variant_barcode = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    variant_sku = models.CharField(max_length=100, blank=True, default="", db_index=True)
    image_url = models.URLField(blank=True, default="")
    sales_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    pos_visible = models.BooleanField(default=True, db_index=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_name"]
        indexes = [
            models.Index(fields=["profile_id", "is_active"]),
            models.Index(fields=["profile_id", "pos_visible"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["profile_id", "variant_barcode"],
                condition=Q(variant_barcode__isnull=False),
                name="inv_catalog_variant_profile_barcode",
            ),
        ]

    def __str__(self):
        return self.display_name
