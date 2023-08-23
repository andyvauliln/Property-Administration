# Generated by Django 4.2.4 on 2023-08-21 12:07

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mysite", "0005_booking_period_alter_cleaning_status_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="period",
            field=models.CharField(
                choices=[("dayly", "Dayly"), ("monthly", "Monthly")],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="booking",
            name="price",
            field=models.DecimalField(decimal_places=2, max_digits=32),
        ),
        migrations.AlterField(
            model_name="booking",
            name="status",
            field=models.CharField(
                choices=[
                    ("confirmed", "Confirmed"),
                    ("canceled", "Canceled"),
                    ("pending", "Pending"),
                ],
                db_index=True,
                default="pending",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="cleaning",
            name="status",
            field=models.CharField(
                choices=[
                    ("scheduled", "Scheduled"),
                    ("completed", "Completed"),
                    ("canceled", "Canceled"),
                ],
                db_index=True,
                default="scheduled",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="notification",
            name="status",
            field=models.CharField(
                choices=[
                    ("done", "Done"),
                    ("pending", "Pending"),
                    ("canceled", "Canceled"),
                ],
                db_index=True,
                default="pending",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="payment",
            name="payment_status",
            field=models.CharField(
                choices=[
                    ("income", "Income"),
                    ("outcome", "Outcome"),
                    ("damage_deposit", "Damage Deposit"),
                    ("hold_deposit", "Hold Deposit"),
                ],
                db_index=True,
                default="pending",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="payment",
            name="payment_type",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("received", "Received"),
                    ("cancelled", "Cancelled"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
    ]
