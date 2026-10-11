from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('orders', '0007_customerreturn_idempotency_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerreturn',
            name='completion_idempotency_key',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Stable key for the completion mutation, unique within the workspace when supplied.',
                max_length=200,
            ),
        ),
        migrations.AddConstraint(
            model_name='customerreturn',
            constraint=models.UniqueConstraint(
                condition=~models.Q(completion_idempotency_key=''),
                fields=('profile_id', 'completion_idempotency_key'),
                name='unique_customer_return_profile_completion_idempotency',
            ),
        ),
    ]
