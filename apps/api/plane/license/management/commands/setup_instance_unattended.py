# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unattended first-boot instance setup for one-click / automated provisioning.

Claims the instance admin and marks setup as done so a freshly provisioned
instance is never left in the open "anyone can become admin" state, and the
customer lands on the normal workspace onboarding instead of the God Mode
setup wizard.

Idempotent: if the instance already has an admin it does nothing, so it is
safe to run on every container boot. Reads from flags or environment variables
(flags win):

  INSTANCE_ADMIN_EMAIL / --admin-email           (required)
  INSTANCE_ADMIN_PASSWORD / --admin-password      (required)
  INSTANCE_ADMIN_FIRST_NAME / --admin-first-name  (default: local part of email)
  INSTANCE_ADMIN_LAST_NAME / --admin-last-name
  INSTANCE_NAME / --instance-name                 (default: "My Company")
  DEFAULT_WORKSPACE_NAME / --workspace-name        (optional; creates a workspace)
  DEFAULT_WORKSPACE_SLUG / --workspace-slug         (optional; derived from name)
  DISABLE_TELEMETRY=1 / --disable-telemetry
  --force                                          (re-claim even if set up)

Example (in a provisioning entrypoint, after register_instance/configure_instance):
  python manage.py setup_instance_unattended --settings=plane.settings.production
"""

import os
import re
import uuid

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from plane.db.models import User, Profile, Workspace, WorkspaceMember
from plane.license.models import Instance, InstanceAdmin


def _opt(options, key, env, default=None):
    return options.get(key) or os.environ.get(env) or default


def _slugify(value):
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return value or "workspace"


class Command(BaseCommand):
    help = "Unattended first-boot setup: claim instance admin + complete instance setup (idempotent)"

    def add_arguments(self, parser):
        parser.add_argument("--admin-email")
        parser.add_argument("--admin-password")
        parser.add_argument("--admin-first-name")
        parser.add_argument("--admin-last-name")
        parser.add_argument("--instance-name")
        parser.add_argument("--workspace-name")
        parser.add_argument("--workspace-slug")
        parser.add_argument("--disable-telemetry", action="store_true", default=False)
        parser.add_argument("--force", action="store_true", default=False)

    def handle(self, *args, **options):
        email = _opt(options, "admin_email", "INSTANCE_ADMIN_EMAIL")
        password = _opt(options, "admin_password", "INSTANCE_ADMIN_PASSWORD")
        if not email or not password:
            raise CommandError(
                "admin email and password are required "
                "(--admin-email/--admin-password or INSTANCE_ADMIN_EMAIL/INSTANCE_ADMIN_PASSWORD)"
            )
        email = email.strip().lower()
        first_name = _opt(options, "admin_first_name", "INSTANCE_ADMIN_FIRST_NAME", email.split("@")[0])
        last_name = _opt(options, "admin_last_name", "INSTANCE_ADMIN_LAST_NAME", "")
        instance_name = _opt(options, "instance_name", "INSTANCE_NAME", "My Company")
        workspace_name = _opt(options, "workspace_name", "DEFAULT_WORKSPACE_NAME")
        workspace_slug = _opt(options, "workspace_slug", "DEFAULT_WORKSPACE_SLUG")
        disable_telemetry = options.get("disable_telemetry") or os.environ.get("DISABLE_TELEMETRY") in ("1", "true", "True")

        instance = Instance.objects.first()
        if instance is None:
            raise CommandError(
                "No instance found. Run 'register_instance' and 'configure_instance' first (the API entrypoint does this)."
            )

        # Idempotency / takeover guard: if already claimed, do nothing.
        if InstanceAdmin.objects.exists() and not options["force"]:
            self.stdout.write(self.style.SUCCESS("Instance already set up (admin exists) — nothing to do."))
            return

        with transaction.atomic():
            user = User.objects.filter(email=email).first()
            created_user = user is None
            if created_user:
                user = User.objects.create(
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    username=uuid.uuid4().hex,
                    password=make_password(password),
                    is_password_autoset=False,
                )
            else:
                user.set_password(password)
                user.is_password_autoset = False
            user.is_active = True
            user.is_email_verified = True
            user.last_active = timezone.now()
            user.token_updated_at = timezone.now()
            user.save()

            # Onboarding state lives on Profile (not User) in this Plane version.
            profile, _ = Profile.objects.get_or_create(user=user, defaults={"company_name": instance_name})
            step = dict(profile.onboarding_step or {})
            step["profile_complete"] = True  # we already captured their name

            made_workspace = False
            slug = None
            if workspace_name:
                slug = _slugify(workspace_slug or workspace_name)
                base, i = slug, 1
                while Workspace.objects.filter(slug=slug).exists():
                    i += 1
                    slug = f"{base}-{i}"
                workspace = Workspace.objects.create(name=workspace_name, slug=slug, owner=user)
                WorkspaceMember.objects.create(workspace=workspace, member=user, role=20)
                step["workspace_create"] = True
                profile.last_workspace_id = workspace.id
                made_workspace = True

            # If a workspace exists, drop them straight in; else let them create one.
            profile.is_onboarded = made_workspace
            profile.is_tour_completed = made_workspace
            profile.onboarding_step = step
            if not profile.company_name:
                profile.company_name = instance_name
            profile.save()

            # Claim the instance admin and complete setup.
            InstanceAdmin.objects.get_or_create(user=user, instance=instance, defaults={"role": 20})
            instance.is_setup_done = True
            instance.is_signup_screen_visited = True
            instance.instance_name = instance_name
            if disable_telemetry:
                instance.is_telemetry_enabled = False
            instance.save()

        self.stdout.write(self.style.SUCCESS("Instance setup completed."))
        self.stdout.write(f"  admin: {email} ({'created' if created_user else 'existing user, claimed'})")
        self.stdout.write(f"  instance_name: {instance_name} | telemetry: {'off' if disable_telemetry else 'on'}")
        if workspace_name:
            self.stdout.write(f"  workspace: {workspace_name} (/{slug})")
        else:
            self.stdout.write("  workspace: none (user will be prompted to create one)")
