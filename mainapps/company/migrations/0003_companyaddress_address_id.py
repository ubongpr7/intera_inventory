from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("company", "0002_alter_contact_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="companyaddress",
            name="address_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
