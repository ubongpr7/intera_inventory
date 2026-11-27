from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("stock", "0006_stockcountsession_stockcountline"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryMcpWorkflow",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("workflow_type", models.CharField(db_index=True, max_length=80)),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("cancelled", "Cancelled")],
                        db_index=True,
                        default="active",
                        max_length=20,
                    ),
                ),
                ("step", models.CharField(db_index=True, max_length=80)),
                ("user_id", models.CharField(db_index=True, max_length=255)),
                ("profile_id", models.BigIntegerField(db_index=True)),
                ("state", models.JSONField(blank=True, default=dict)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.AddIndex(
            model_name="inventorymcpworkflow",
            index=models.Index(fields=["profile_id", "workflow_type", "status"], name="stock_inven_profile_ca1693_idx"),
        ),
        migrations.AddIndex(
            model_name="inventorymcpworkflow",
            index=models.Index(fields=["user_id", "workflow_type", "status"], name="stock_inven_user_id_ffb26e_idx"),
        ),
    ]
