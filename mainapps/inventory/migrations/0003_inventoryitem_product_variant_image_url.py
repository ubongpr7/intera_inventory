from django.db import migrations, models


def backfill_variant_image_urls(apps, schema_editor):
    InventoryItem = apps.get_model("inventory", "InventoryItem")
    CatalogVariantProjection = apps.get_model("projections", "CatalogVariantProjection")

    projections = {
        str(projection.variant_id): projection.image_url or ""
        for projection in CatalogVariantProjection.objects.exclude(image_url="")
    }
    if not projections:
        return

    for item in InventoryItem.objects.exclude(product_variant_id__isnull=True):
        image_url = projections.get(str(item.product_variant_id))
        if image_url and item.product_variant_image_url != image_url:
            item.product_variant_image_url = image_url
            item.save(update_fields=["product_variant_image_url"])


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_initial"),
        ("projections", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryitem",
            name="product_variant_image_url",
            field=models.URLField(blank=True, default="", verbose_name="Product Variant Image URL"),
        ),
        migrations.RunPython(backfill_variant_image_urls, migrations.RunPython.noop),
    ]
