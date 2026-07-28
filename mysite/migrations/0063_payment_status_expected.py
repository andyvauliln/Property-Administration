from django.db import migrations, models


def completed_to_expected(apps, schema_editor):
    Payment = apps.get_model('mysite', 'Payment')
    Payment.objects.filter(payment_status='Completed').update(payment_status='Expected')


def expected_to_completed(apps, schema_editor):
    Payment = apps.get_model('mysite', 'Payment')
    Payment.objects.filter(payment_status='Expected').update(payment_status='Completed')


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0062_booking_payment_source_fields'),
    ]

    operations = [
        migrations.RunPython(completed_to_expected, expected_to_completed),
        migrations.AlterField(
            model_name='payment',
            name='payment_status',
            field=models.CharField(
                choices=[
                    ('Pending', 'Pending'),
                    ('Expected', 'Expected'),
                    ('Merged', 'Merged'),
                ],
                db_index=True,
                default='Pending',
                max_length=32,
            ),
        ),
    ]
