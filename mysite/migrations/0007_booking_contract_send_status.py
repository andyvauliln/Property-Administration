# Generated by Django 4.2.4 on 2024-03-15 19:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0006_booking_contract_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='contract_send_status',
            field=models.TextField(blank=True, default='Not Sent', null=True),
        ),
    ]
