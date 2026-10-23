"""FORK: time-tracking-v2 S5 — ``ResourceCapacity.weekly_capacity`` becomes a nullable
per-person override (null = the workspace work week).

Rows holding the old model default (2400 = 40 h) are set to null: that value is the
workspace default work week (Mon-Fri x 8 h) and no workspace has a different work week
yet (the settings row is new in 0128), so nothing changes today, and those people follow
the workspace if its work week is changed later. Any other value is a deliberate
part-time override and is kept. Reverse fills nulls back with 2400.
"""

from django.db import migrations, models


def clear_default_overrides(apps, schema_editor):
    ResourceCapacity = apps.get_model("db", "ResourceCapacity")
    ResourceCapacity.objects.filter(weekly_capacity=2400).update(weekly_capacity=None)


def fill_nulls(apps, schema_editor):
    ResourceCapacity = apps.get_model("db", "ResourceCapacity")
    ResourceCapacity.objects.filter(weekly_capacity__isnull=True).update(weekly_capacity=2400)


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0130_time_tracking_v2_estimates"),
    ]

    operations = [
        migrations.AlterField(
            model_name="resourcecapacity",
            name="weekly_capacity",
            field=models.PositiveIntegerField(blank=True, default=None, null=True),
        ),
        migrations.RunPython(clear_default_overrides, fill_nulls),
    ]
