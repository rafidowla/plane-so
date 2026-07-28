# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Unit tests for send_email_notification's origin-cache fallback.

Regression guard: the Redis key holding the triggering request's origin (set
with a 10-minute TTL) is only used to build links inside the email body — not
an auth token, not tied to that specific request. A backlog longer than the
TTL used to silently drop the email forever instead of falling back to the
deployment's configured app URL.
"""

import pytest
from unittest.mock import MagicMock, patch

from plane.bgtasks.email_notification_task import send_email_notification
from plane.db.models import EmailNotificationLog, Issue, ProjectIdentifier, State
from plane.tests.factories import ProjectFactory, UserFactory, WorkspaceFactory


def _make_issue(workspace, project, actor):
    ProjectIdentifier.objects.get_or_create(workspace=workspace, project=project, name=project.name[:5].upper())
    state = State.objects.create(workspace=workspace, project=project, name="Todo", group="unstarted", default=True)
    issue = Issue(workspace=workspace, project=project, name="Fix the thing", state=state)
    issue.save(created_by_id=actor.id)
    return issue


def _notification_data(actor_id):
    return {
        str(actor_id): [
            {
                "issue_activity": {
                    "field": "state",
                    "old_value": "Todo",
                    "new_value": "Done",
                    "activity_time": "2026-01-01T00:00:00.000Z",
                }
            }
        ]
    }


@pytest.mark.unit
@pytest.mark.django_db
class TestSendEmailNotificationOriginFallback:
    @patch("plane.bgtasks.email_notification_task.EmailMultiAlternatives")
    @patch("plane.bgtasks.email_notification_task.get_connection")
    @patch("plane.bgtasks.email_notification_task.get_email_configuration")
    @patch("plane.bgtasks.email_notification_task.redis_instance")
    def test_falls_back_to_app_base_url_on_cache_miss(
        self, mock_redis_instance, mock_email_config, mock_get_connection, mock_email_cls, settings
    ):
        settings.APP_BASE_URL = "https://app.example.com"
        actor = UserFactory(username="fallback-actor")
        receiver = UserFactory(username="fallback-receiver")
        workspace = WorkspaceFactory(owner=actor)
        project = ProjectFactory(workspace=workspace, name="Fallback Project")
        issue = _make_issue(workspace, project, actor)

        # Simulate a Redis miss (key expired/never set) rather than a hit.
        redis_client = MagicMock()
        redis_client.get.return_value = None
        mock_redis_instance.return_value = redis_client

        mock_email_config.return_value = (
            "smtp.example.com",
            "user",
            "pass",
            "587",
            "1",
            "0",
            "from@example.com",
        )
        sent_email = MagicMock()
        mock_email_cls.return_value = sent_email

        log = EmailNotificationLog.objects.create(
            receiver=receiver,
            triggered_by=actor,
            entity_identifier=issue.id,
            entity_name="issue",
            entity="issue",
        )

        send_email_notification(
            issue_id=str(issue.id),
            notification_data=_notification_data(actor.id),
            receiver_id=str(receiver.id),
            email_notification_ids=[log.id],
        )

        # The email still sends despite the cache miss...
        sent_email.send.assert_called_once()
        # ...using links built from the configured app URL, not a dropped/blank origin.
        html_content = sent_email.attach_alternative.call_args[0][0]
        assert "https://app.example.com" in html_content

        log.refresh_from_db()
        assert log.sent_at is not None

    @patch("plane.bgtasks.email_notification_task.EmailMultiAlternatives")
    @patch("plane.bgtasks.email_notification_task.get_connection")
    @patch("plane.bgtasks.email_notification_task.get_email_configuration")
    @patch("plane.bgtasks.email_notification_task.redis_instance")
    def test_uses_cached_origin_when_present(
        self, mock_redis_instance, mock_email_config, mock_get_connection, mock_email_cls, settings
    ):
        settings.APP_BASE_URL = "https://app.example.com"
        actor = UserFactory(username="cachehit-actor")
        receiver = UserFactory(username="cachehit-receiver")
        workspace = WorkspaceFactory(owner=actor)
        project = ProjectFactory(workspace=workspace, name="Cache Hit Project")
        issue = _make_issue(workspace, project, actor)

        redis_client = MagicMock()
        redis_client.get.return_value = b"https://cached-origin.example.com"
        mock_redis_instance.return_value = redis_client

        mock_email_config.return_value = (
            "smtp.example.com",
            "user",
            "pass",
            "587",
            "1",
            "0",
            "from@example.com",
        )
        sent_email = MagicMock()
        mock_email_cls.return_value = sent_email

        log = EmailNotificationLog.objects.create(
            receiver=receiver,
            triggered_by=actor,
            entity_identifier=issue.id,
            entity_name="issue",
            entity="issue",
        )

        send_email_notification(
            issue_id=str(issue.id),
            notification_data=_notification_data(actor.id),
            receiver_id=str(receiver.id),
            email_notification_ids=[log.id],
        )

        sent_email.send.assert_called_once()
        html_content = sent_email.attach_alternative.call_args[0][0]
        assert "https://cached-origin.example.com" in html_content
        assert "https://app.example.com" not in html_content
