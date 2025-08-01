# Generated by Django 5.2.4 on 2025-07-16 23:04

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0004_alter_purchaseorder_received_date_and_more'),
        ('stock', '0002_stockitem_product_variant'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='batch_number',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='expiry_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='fully_received',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='purchaseorderlineitem',
            name='manufactured_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='purchaseorderlineitem',
            name='stock_item',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='po_line_items', to='stock.stockitem'),
        ),
    ]
