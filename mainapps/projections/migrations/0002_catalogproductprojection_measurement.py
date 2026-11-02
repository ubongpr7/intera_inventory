from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("projections", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogproductprojection",
            name="measurement_type",
            field=models.CharField(default="count", max_length=20),
        ),
        migrations.AddField(
            model_name="catalogproductprojection",
            name="uom_definitions",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
