from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("identity", "0004_identitycompanyprofile_headquarters_address_id")]

    operations = [
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("profile_id", models.BigIntegerField(db_index=True)),
                ("operation", models.CharField(max_length=160)),
                ("key", models.CharField(max_length=255)),
                ("request_hash", models.CharField(max_length=64)),
                ("status", models.CharField(default="processing", max_length=20)),
                ("response_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("response_body", models.JSONField(blank=True, null=True)),
                ("resource_type", models.CharField(blank=True, default="", max_length=120)),
                ("resource_id", models.CharField(blank=True, default="", max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["profile_id", "status"], name="identity_id_profile_3217af_idx"),
                    models.Index(fields=["created_at"], name="identity_id_created_84376e_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("profile_id", "operation", "key"),
                        name="unique_idempotency_profile_operation_key",
                    )
                ],
            },
        )
    ]
