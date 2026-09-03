from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="InventoryBootstrap",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("profile_id", models.BigIntegerField(db_index=True)),
                ("application_slug", models.CharField(max_length=100)),
                ("subscription_id", models.CharField(max_length=100)),
                ("activation_event_id", models.CharField(max_length=150)),
                ("bootstrap_version", models.CharField(max_length=50)),
                ("status", models.CharField(choices=[("completed", "Completed"), ("rejected", "Rejected")], default="completed", max_length=20)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("profile_id", "application_slug"), name="inventory_bootstrap_profile_application_unique")],
                "indexes": [
                    models.Index(fields=["application_slug", "status"], name="sb_app_status_idx"),
                    models.Index(fields=["subscription_id"], name="sb_subscription_idx"),
                ],
            },
        ),
    ]
