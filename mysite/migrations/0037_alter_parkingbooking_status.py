# Generated by Django 4.2.4 on 2025-06-14 08:40

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0036_alter_apartment_raiting'),
    ]

    operations = [
        migrations.AlterField(
            model_name='parkingbooking',
            name='status',
            field=models.CharField(blank=True, choices=[('Unavailable', 'Unavailable'), ('Booked', 'Booked'), ('No Car', 'No Car')], max_length=255, null=True),
        ),
    ]
