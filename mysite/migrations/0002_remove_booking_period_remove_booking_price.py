# Generated by Django 4.2.4 on 2023-09-12 05:43

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("mysite", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="booking",
            name="period",
        ),
        migrations.RemoveField(
            model_name="booking",
            name="price",
        ),
    ]
