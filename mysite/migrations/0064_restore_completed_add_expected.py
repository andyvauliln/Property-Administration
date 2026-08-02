from django.db import migrations, models


def restore_completed_status(apps, schema_editor):
    Payment = apps.get_model('mysite', 'Payment')
    Payment.objects.filter(payment_status='Expected').update(payment_status='Completed')


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0063_payment_status_expected'),
    ]

    operations = [
        migrations.RunPython(restore_completed_status, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='payment',
            name='payment_status',
            field=models.CharField(
                choices=[
                    ('Pending', 'Pending'),
                    ('Completed', 'Completed'),
                    ('Expected', 'Expected'),
                    ('Merged', 'Merged'),
                ],
                db_index=True,
                default='Pending',
                max_length=32,
            ),
        ),
    ]
