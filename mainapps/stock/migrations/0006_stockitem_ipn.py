# Generated by Django 5.2.2 on 2025-06-24 04:52

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock', '0005_alter_stockitem_inventory'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockitem',
            name='IPN',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
