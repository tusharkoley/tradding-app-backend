# Generated by Django 5.1.4 on 2025-01-25 21:06

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0006_profile_is_active"),
    ]

    operations = [
        migrations.AddField(
            model_name="profile",
            name="is_staff",
            field=models.BooleanField(default=False),
        ),
    ]
