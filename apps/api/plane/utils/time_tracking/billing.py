# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 secfix — who may see or set an entry's billing fields.

``is_billable`` / ``billable_rate`` / ``currency`` on a time entry are business data:
they are returned (and writable) only while the workspace's ``billing_enabled`` setting
is on, and only to a workspace admin or an admin of the entry's project. Every response
path that serializes a worklog goes through ``IssueWorklogSerializer``, which strips
these fields unless it was given a ``rate_viewer`` that passes ``RateViewer.can_see``
(deny by default).
"""

# Python imports
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Module imports
from plane.utils.time_tracking import limits
from plane.utils.time_tracking.approvers import is_project_admin, is_workspace_admin
from plane.utils.time_tracking.timer import TimeTrackingError

RATE_FIELDS = ("is_billable", "billable_rate", "currency")
MAX_RATE = Decimal("9999999999.99")  # DecimalField(max_digits=12, decimal_places=2)
_CURRENCY = re.compile(r"^[A-Za-z]{3}$")


def billing_enabled(workspace_id):
    return bool(limits.read_settings(workspace_id).billing_enabled)


def can_manage_rates(workspace_id, user, project_id):
    """Billing is on and ``user`` is a workspace admin or an admin of ``project_id``."""
    if user is None or getattr(user, "is_anonymous", True):
        return False
    if not billing_enabled(workspace_id):
        return False
    return is_workspace_admin(workspace_id, user.id) or bool(project_id and is_project_admin(project_id, user.id))


class RateViewer:
    """Per-request memo of ``can_manage_rates`` (a list response checks each project once)."""

    def __init__(self, user):
        self.user = user
        self._cache = {}

    def can_see(self, workspace_id, project_id):
        key = (workspace_id, project_id)
        if key not in self._cache:
            self._cache[key] = can_manage_rates(workspace_id, self.user, project_id)
        return self._cache[key]


def serializer_context(user):
    return {"rate_viewer": RateViewer(user)}


def parse_rate(value):
    """A billable rate as a 2-dp Decimal >= 0 (None / "" = no rate). Raises 400 ``invalid_rate``."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, bool):
            raise InvalidOperation
        rate = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        rate = None
    if rate is None or not rate.is_finite() or rate < 0:
        raise TimeTrackingError({"error": "billable_rate must be a number of 0 or more.", "code": "invalid_rate"}, 400)
    rate = rate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rate > MAX_RATE:
        raise TimeTrackingError({"error": "billable_rate is too large.", "code": "invalid_rate"}, 400)
    return rate


def parse_currency(value):
    """A 3-letter currency code, upper-cased (None / "" = USD). Raises 400 ``invalid_currency``."""
    if value is None or value == "":
        return "USD"
    if not isinstance(value, str) or not _CURRENCY.match(value.strip()):
        raise TimeTrackingError(
            {"error": "currency must be a 3-letter code such as USD.", "code": "invalid_currency"}, 400
        )
    return value.strip().upper()


def parse_billable(value):
    return value if isinstance(value, bool) else str(value).lower() in ("1", "true")
