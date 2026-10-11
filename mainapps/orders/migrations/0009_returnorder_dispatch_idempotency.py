from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0008_customerreturn_completion_idempotency'),
    ]

    operations = [
        migrations.AddField(
            model_name='returnorder',
            name='dispatch_idempotency_key',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddConstraint(
            model_name='returnorder',
            constraint=models.UniqueConstraint(
                condition=~models.Q(dispatch_idempotency_key=''),
                fields=('profile_id', 'dispatch_idempotency_key'),
                name='unique_return_order_profile_dispatch_idempotency',
            ),
        ),
    ]
