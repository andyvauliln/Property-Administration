from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0050_alter_apartment_raiting'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatMessageTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(db_index=True, max_length=120)),
                ('body', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by_user', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='chat_message_templates', to='mysite.user')),
            ],
            options={
                'ordering': ['name', '-created_at'],
            },
        ),
    ]

