"""FORK: time-tracking-v2 secfix — ``time_tracking_audit_logs`` is append-only in the database.

``AUDIT_GUARD_SQL`` installs a row trigger that refuses every UPDATE and DELETE, with two
exceptions that Postgres runs from its own referential-action triggers
(``pg_trigger_depth() > 1``): the ``ON DELETE CASCADE`` of a workspace hard delete, and the
``ON DELETE SET NULL`` of a user hard delete (which may only null ``actor_id`` and change
nothing else). Both foreign keys are therefore created in SQL and the Django fields become
DO_NOTHING / ``db_constraint=False`` (Django's own cascade would issue a plain DELETE /
UPDATE, which the trigger refuses) — the same shape as migration 0129 uses for request
events. Tests run with ``--nomigrations`` and install the same SQL from this module.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


AUDIT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION time_tracking_audit_logs_append_only() RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() > 1 THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        IF NEW.actor_id IS NULL AND (to_jsonb(NEW) - 'actor_id') = (to_jsonb(OLD) - 'actor_id') THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'time_tracking_audit_logs is append-only (% refused)', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS time_tracking_audit_logs_append_only ON time_tracking_audit_logs;
CREATE TRIGGER time_tracking_audit_logs_append_only
    BEFORE UPDATE OR DELETE ON time_tracking_audit_logs
    FOR EACH ROW EXECUTE FUNCTION time_tracking_audit_logs_append_only();

ALTER TABLE time_tracking_audit_logs DROP CONSTRAINT IF EXISTS time_tracking_audit_logs_workspace_fk;
ALTER TABLE time_tracking_audit_logs
    ADD CONSTRAINT time_tracking_audit_logs_workspace_fk
    FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE;
ALTER TABLE time_tracking_audit_logs DROP CONSTRAINT IF EXISTS time_tracking_audit_logs_actor_fk;
ALTER TABLE time_tracking_audit_logs
    ADD CONSTRAINT time_tracking_audit_logs_actor_fk
    FOREIGN KEY (actor_id) REFERENCES users (id) ON DELETE SET NULL;
"""

AUDIT_GUARD_REVERSE_SQL = """
ALTER TABLE time_tracking_audit_logs DROP CONSTRAINT IF EXISTS time_tracking_audit_logs_actor_fk;
ALTER TABLE time_tracking_audit_logs DROP CONSTRAINT IF EXISTS time_tracking_audit_logs_workspace_fk;
DROP TRIGGER IF EXISTS time_tracking_audit_logs_append_only ON time_tracking_audit_logs;
DROP FUNCTION IF EXISTS time_tracking_audit_logs_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0131_time_tracking_v2_reports"),
    ]

    operations = [
        migrations.AlterField(
            model_name="timetrackingauditlog",
            name="workspace",
            field=models.ForeignKey(
                db_constraint=False,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="time_tracking_audit_logs",
                to="db.workspace",
            ),
        ),
        migrations.AlterField(
            model_name="timetrackingauditlog",
            name="actor",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="time_tracking_audit_logs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunSQL(AUDIT_GUARD_SQL, reverse_sql=AUDIT_GUARD_REVERSE_SQL),
    ]
