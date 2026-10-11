from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0006_customerreturnline_stock_quantity_received_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerreturn',
            name='idempotency_key',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Client mutation key, unique within the workspace when supplied.',
                max_length=200,
            ),
        ),
        migrations.AddConstraint(
            model_name='customerreturn',
            constraint=models.UniqueConstraint(
                condition=~models.Q(('idempotency_key', '')),
                fields=('profile_id', 'idempotency_key'),
                name='unique_customer_return_profile_idempotency',
            ),
        ),
    ]
