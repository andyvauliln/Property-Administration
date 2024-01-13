# Generated by Django 4.2.4 on 2023-09-27 18:01

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mysite", "0017_alter_booking_visit_purpose"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="visit_purpose",
            field=models.CharField(
                blank=True,
                choices=[
                    ("Tourism", "Tourism"),
                    ("Work", "Work"),
                    ("Medical", "Medical"),
                    ("Between Houses", "Between Houses"),
                    ("Snow Bird", "Snow Bird"),
                    ("Other", "Other"),
                ],
                max_length=32,
            ),
        ),
    ]
