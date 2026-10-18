"""FORK: time-tracking-v2 holidays/leave (PSR-122) — holiday calendars, holidays, per-person
calendar assignment and per-person leave, plus the new audit target values.

Reverse: drops the four tables (they only feed Expected hours; nothing else depends on them).
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0133_time_estimate_grandfather"),
    ]

    operations = [
        migrations.AlterField(
            model_name="timetrackingauditlog",
            name="target_type",
            field=models.CharField(
                choices=[
                    ("worklog", "Worklog"),
                    ("member_status", "Member status"),
                    ("setting", "Setting"),
                    ("estimate", "Estimate"),
                    ("holiday_calendar", "Holiday calendar"),
                    ("holiday", "Holiday"),
                    ("leave", "Leave"),
                    ("member_calendar", "Member holiday calendar"),
                ],
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="HolidayCalendar",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("is_default", models.BooleanField(default=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="holiday_calendars", to="db.workspace"
                    ),
                ),
            ],
            options={
                "verbose_name": "Holiday Calendar",
                "verbose_name_plural": "Holiday Calendars",
                "db_table": "holiday_calendars",
                "ordering": ("-is_default", "name"),
            },
        ),
        migrations.CreateModel(
            name="Holiday",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("date", models.DateField()),
                ("name", models.CharField(max_length=200)),
                (
                    "portion",
                    models.CharField(
                        choices=[("full", "Full day"), ("half", "Half day")], default="full", max_length=4
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "calendar",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="holidays", to="db.holidaycalendar"
                    ),
                ),
            ],
            options={
                "verbose_name": "Holiday",
                "verbose_name_plural": "Holidays",
                "db_table": "holidays",
                "ordering": ("date", "created_at"),
            },
        ),
        migrations.CreateModel(
            name="MemberHolidayCalendar",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                (
                    "calendar",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="member_rows", to="db.holidaycalendar"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="holiday_calendar_rows",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="member_holiday_calendars",
                        to="db.workspace",
                    ),
                ),
            ],
            options={
                "verbose_name": "Member Holiday Calendar",
                "verbose_name_plural": "Member Holiday Calendars",
                "db_table": "member_holiday_calendars",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="MemberLeave",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Last Modified At")),
                ("deleted_at", models.DateTimeField(blank=True, null=True, verbose_name="Deleted At")),
                (
                    "id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                (
                    "portion",
                    models.CharField(
                        choices=[("full", "Full day"), ("half", "Half day")], default="full", max_length=4
                    ),
                ),
                ("note", models.CharField(blank=True, default="", max_length=500)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_created_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_updated_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Last Modified By",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="time_leaves",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="member_leaves", to="db.workspace"
                    ),
                ),
            ],
            options={
                "verbose_name": "Member Leave",
                "verbose_name_plural": "Member Leaves",
                "db_table": "member_leaves",
                "ordering": ("-start_date", "-created_at"),
            },
        ),
        migrations.AddConstraint(
            model_name="holidaycalendar",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("workspace", "name"),
                name="holiday_calendar_unique_name_when_deleted_at_null",
            ),
        ),
        migrations.AddConstraint(
            model_name="holidaycalendar",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True), ("is_default", True)),
                fields=("workspace",),
                name="holiday_calendar_one_default_when_deleted_at_null",
            ),
        ),
        migrations.AddConstraint(
            model_name="holiday",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("calendar", "date"),
                name="holiday_unique_calendar_date_when_deleted_at_null",
            ),
        ),
        migrations.AddConstraint(
            model_name="memberholidaycalendar",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("workspace", "user"),
                name="member_holiday_calendar_unique_when_deleted_at_null",
            ),
        ),
        migrations.AddIndex(
            model_name="memberleave",
            index=models.Index(fields=["workspace", "user", "start_date", "end_date"], name="member_leave_range_idx"),
        ),
        migrations.AddConstraint(
            model_name="memberleave",
            constraint=models.CheckConstraint(
                condition=models.Q(("end_date__gte", models.F("start_date"))), name="member_leave_range"
            ),
        ),
    ]
