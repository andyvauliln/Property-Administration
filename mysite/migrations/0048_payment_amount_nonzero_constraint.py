from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0047_systemlog_errorlog'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='payment',
            constraint=models.CheckConstraint(
                check=~models.Q(amount=0),
                name='payment_amount_nonzero',
            ),
        ),
    ]

