from django.db import migrations, models
from django.conf import settings


def migrate_manager_to_managers(apps, schema_editor):
    """
    Data migration: Copy existing manager FK to new managers M2M field
    """
    Apartment = apps.get_model('mysite', 'Apartment')
    for apartment in Apartment.objects.filter(manager__isnull=False):
        apartment.managers.add(apartment.manager)


def reverse_migrate_managers_to_manager(apps, schema_editor):
    """
    Reverse migration: Copy first manager from M2M back to FK
    """
    Apartment = apps.get_model('mysite', 'Apartment')
    for apartment in Apartment.objects.all():
        first_manager = apartment.managers.first()
        if first_manager:
            apartment.manager = first_manager
            apartment.save()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('mysite', '0048_payment_amount_nonzero_constraint'),
    ]

    operations = [
        # Step 1: Add the new ManyToMany field
        migrations.AddField(
            model_name='apartment',
            name='managers',
            field=models.ManyToManyField(
                blank=True,
                limit_choices_to={'role': 'Manager'},
                related_name='managed_apartments_new',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Step 2: Run data migration to copy existing manager to managers
        migrations.RunPython(migrate_manager_to_managers, reverse_migrate_managers_to_manager),
        # Step 3: Remove the old manager ForeignKey
        migrations.RemoveField(
            model_name='apartment',
            name='manager',
        ),
        # Step 4: Rename the related_name to final value
        migrations.AlterField(
            model_name='apartment',
            name='managers',
            field=models.ManyToManyField(
                blank=True,
                limit_choices_to={'role': 'Manager'},
                related_name='managed_apartments',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]



