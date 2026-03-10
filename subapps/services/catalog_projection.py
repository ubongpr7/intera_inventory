import uuid

from django.core.cache import cache
from typing import Optional, Dict, Any

from mainapps.projections.models import CatalogVariantProjection


class CatalogProjectionLookup:
    """Access local catalog projections hydrated by Kafka consumers."""

    CACHE_TIMEOUT = 300

    @classmethod
    def get_variant_details_by_barcode(cls, barcode: str, request=None) -> Optional[Dict[str, Any]]:
        if not barcode:
            return None

        cache_key = f"product_variant_projection_{barcode}"
        cached_data = cache.get(cache_key)
        if cached_data:
            return cached_data

        queryset = CatalogVariantProjection.objects.select_related("product")
        variant = queryset.filter(variant_barcode=barcode).first()

        if variant is None:
            try:
                variant_uuid = uuid.UUID(str(barcode))
            except (TypeError, ValueError, AttributeError):
                variant_uuid = None

            if variant_uuid is not None:
                variant = queryset.filter(variant_id=variant_uuid).first()

        if variant is None:
            return None

        data = {
            "id": str(variant.variant_id),
            "display_name": variant.display_name,
            "display_image": variant.image_url,
            "image": variant.image_url,
            "selling_price": str(variant.sales_price),
            "variant_barcode": variant.variant_barcode,
            "variant_sku": variant.variant_sku,
            "product_details": {
                "id": str(variant.product_id),
                "name": variant.product.name,
                "category": variant.product.category_name,
                "tax_rate": str(variant.product.tax_rate),
                "track_stock": variant.product.track_stock,
            },
        }
        cache.set(cache_key, data, cls.CACHE_TIMEOUT)
        return data
