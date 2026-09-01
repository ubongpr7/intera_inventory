from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0005_backfill_inventory_placements"),
    ]

    operations = [
        migrations.AddField(
            model_name="address",
            name="address_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
