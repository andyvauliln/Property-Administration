# Generated by Django 4.2.4 on 2025-01-29 17:34

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0024_apartmentparking'),
    ]

    operations = [
        migrations.AddField(
            model_name='apartmentparking',
            name='end_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='apartmentparking',
            name='start_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]
