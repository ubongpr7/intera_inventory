from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("manufacturing", "0009_workcenterblock")]

    operations = [
        migrations.AddField(
            model_name="productionorder",
            name="idempotency_key",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddConstraint(
            model_name="productionorder",
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=""),
                fields=("profile_id", "idempotency_key"),
                name="unique_production_order_profile_idempotency",
            ),
        ),
    ]
