from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("identity", "0003_identitycompanyprofile_headquarters_address")]

    operations = [
        migrations.AddField(
            model_name="identitycompanyprofile",
            name="headquarters_address_id",
            field=models.UUIDField(blank=True, db_index=True, null=True, verbose_name="Headquarters Address ID"),
        ),
    ]
