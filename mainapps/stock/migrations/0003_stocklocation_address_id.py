from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stock", "0002_stocklocation_is_default_structural_location"),
    ]

    operations = [
        migrations.AddField(
            model_name="stocklocation",
            name="address_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
