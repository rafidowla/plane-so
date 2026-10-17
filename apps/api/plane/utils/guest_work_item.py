# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: guest-access PSR-119 — guests raise and edit their own work items.

Shared by the app (``plane.app.views``) and the public ``/api/v1/`` views so both enforce exactly the same
rules: an explicit field allow-list, own items only, no delete, and no client-forged authorship.
"""

# Python imports
from uuid import UUID

# Django imports
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

# Third party imports
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE
from plane.db.models import Label, ProjectMember, WorkspaceMember


# Explicit allow-list: the only work item fields a project Guest may send on create/update.
GUEST_WORK_ITEM_FIELDS = frozenset(
    {
        "name",
        "description",
        "description_html",
        "description_json",
        "description_binary",
        "description_stripped",
        "priority",
        "labels",
        "label_ids",
    }
)

# Keys a Guest's body may carry that are dropped without error: the URL is authoritative for id / project,
# ``skip_activity`` is the web editor's description-migration flag (guest edits always record activity), and
# ``sort_order`` is sent by kanban/spreadsheet drag-and-drop (``0`` is neither empty nor allowed, so it must be
# listed). Ignored means dropped from the payload: it is never written.
GUEST_IGNORED_FIELDS = frozenset({"id", "project_id", "project", "skip_activity", "sort_order"})

GUEST_DELETE_DENIED_MESSAGE = "Guests cannot delete work items"
GUEST_EDIT_DENIED_MESSAGE = "Guests can only edit work items they created"


def is_project_guest(user, project_id) -> bool:
    """True when ``user`` is an active Guest of the project (whatever ``guest_view_all_features`` says)."""
    return ProjectMember.objects.filter(
        project_id=project_id, member=user, is_active=True, role=ROLE.GUEST.value
    ).exists()


def is_workspace_admin(user, slug) -> bool:
    return WorkspaceMember.objects.filter(
        member=user, workspace__slug=slug, is_active=True, role=ROLE.ADMIN.value
    ).exists()


def _is_empty(value) -> bool:
    """Empty / default values a guest's form may send for a field it cannot set; these are ignored."""
    return value is None or value is False or value == "" or value == [] or value == {}


def _label_ids_error(project_id, label_ids):
    """Guests may only use labels that already exist in the project (they can never create labels)."""
    if label_ids is None:
        return None
    if not isinstance(label_ids, (list, tuple)):
        return "label_ids must be a list of label ids"
    try:
        wanted = {UUID(str(label_id)) for label_id in label_ids}
    except (ValueError, TypeError, AttributeError):
        return "label_ids must be a list of valid label ids"
    found = set(Label.objects.filter(project_id=project_id, id__in=wanted).values_list("id", flat=True))
    if wanted - found:
        return "Labels {} do not belong to this project".format(sorted(str(i) for i in wanted - found))
    return None


def guest_work_item_payload(request, project_id, data=None):
    """Apply the guest field allow-list to a work item create/update body.

    Returns ``(payload, error_response)``. For anyone who is not an active project Guest the body is
    returned untouched (``data`` or ``request.data``). For a Guest:

    * keys outside ``GUEST_WORK_ITEM_FIELDS`` with a non-empty value -> 400 naming the field(s), with
      ``{"error": ..., "disallowed_fields": [...]}``;
    * keys outside the allow-list with an empty value (``None``, ``""``, ``[]``, ``{}``, ``false``) are
      dropped, so a web form that always posts ``state_id: null`` / ``assignee_ids: []`` keeps working;
    * ``labels`` / ``label_ids`` must already exist in the project.
    """
    body = request.data if data is None else data
    if not is_project_guest(request.user, project_id):
        return body, None
    if not hasattr(body, "items"):
        return body, None  # non-object bodies are rejected by the serializer as usual
    payload = {}
    disallowed = []
    for key, value in body.items():
        if key in GUEST_WORK_ITEM_FIELDS:
            payload[key] = value
        elif key in GUEST_IGNORED_FIELDS:
            continue
        elif not _is_empty(value):
            disallowed.append(key)
    if disallowed:
        disallowed.sort()
        return None, Response(
            {
                "error": "Guests cannot set these fields: {}".format(", ".join(disallowed)),
                "disallowed_fields": disallowed,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    for key in ("labels", "label_ids"):
        message = _label_ids_error(project_id, payload.get(key))
        if message:
            return None, Response({"error": message, key: [message]}, status=status.HTTP_400_BAD_REQUEST)
    return payload, None


def is_restricted_guest(user, project_id) -> bool:
    """True when ``user`` is an active project Guest and the project hides other people's work items."""
    return ProjectMember.objects.filter(
        project_id=project_id,
        member=user,
        is_active=True,
        role=ROLE.GUEST.value,
        project__guest_view_all_features=False,
    ).exists()


def guest_count_q(user, project_id, issue_path="") -> Q:
    """Extra ``filter`` for a module/cycle aggregate (``total_issues``, ``completed_estimates`` ...).

    A restricted guest only counts the work items they could open, i.e. their own; everyone else counts
    everything (empty ``Q``). ``issue_path`` is the lookup from the aggregated model to its work item,
    e.g. ``"issue_module__issue"``. Leave it empty when filtering ``Issue`` itself (``Issue.issue_objects.filter(q)``).
    """
    if is_restricted_guest(user, project_id):
        return Q(**{f"{issue_path}__created_by" if issue_path else "created_by": user})
    return Q()


def restricted_guest_annotation(user, project_ref="pk"):
    """``Exists`` expression: ``user`` is an active Guest of the row's project and it hides others' work items.

    For cross-project querysets (workspace-wide lists), where ``guest_count_q`` -- which takes one project id --
    cannot be used. ``project_ref`` is the row's project id (``"pk"`` on ``Project``, ``"project_id"`` on a
    ``Module`` / ``Cycle``). Annotate it, then build the aggregate filter with ``guest_row_scope_q``.
    """
    return Exists(
        ProjectMember.objects.filter(
            project_id=OuterRef(project_ref),
            member=user,
            is_active=True,
            role=ROLE.GUEST.value,
            project__guest_view_all_features=False,
        )
    )


def guest_row_scope_q(user, annotation_name, creator_path="created_by") -> Q:
    """Aggregate ``filter`` for a queryset annotated with ``restricted_guest_annotation(..)`` as ``annotation_name``.

    Rows whose project hides others' work items only count items ``user`` created (``creator_path`` is the lookup
    to that creator, e.g. ``"issue_module__issue__created_by"``); every other row counts everything.
    """
    return Q(**{annotation_name: False}) | Q(**{creator_path: user})


def guest_visible_issues_q(user, project_ref="project_id", creator_path="created_by") -> Q:
    """``filter`` for a cross-project queryset: drops others' work items in projects that hide them from ``user``.

    Unlike ``visible_issues_q`` (which also requires project membership) this only *removes* what a restricted
    guest may not see, so workspace admins who are not project members keep their counts. ``project_ref`` is
    the lookup to the project id and ``creator_path`` the lookup to the work item creator on the queried model.
    """
    return Q(**{creator_path: user}) | ~restricted_guest_annotation(user, project_ref)


def guest_active_project_q(user, slug, project_ref="project_id") -> Q:
    """``filter`` for a workspace-wide queryset: a workspace Guest only sees projects they still belong to.

    Empty ``Q`` for everyone else (workspace members and admins keep today's behaviour). ``project_ref`` is the
    lookup to the project id on the queried model, e.g. ``"project_id"`` or ``"issue__project_id"``.
    """
    if WorkspaceMember.objects.filter(
        member=user, workspace__slug=slug, is_active=True, role=ROLE.GUEST.value
    ).exists():
        return Q(
            **{
                f"{project_ref}__in": ProjectMember.objects.filter(
                    member=user, workspace__slug=slug, is_active=True
                ).values("project_id")
            }
        )
    return Q()


def guest_edit_denied(user, project_id, created_by_id):
    """403 ``Response`` when a Guest tries to edit a work item they did not create, else ``None``."""
    if created_by_id != user.id and is_project_guest(user, project_id):
        return Response({"error": GUEST_EDIT_DENIED_MESSAGE}, status=status.HTTP_403_FORBIDDEN)
    return None


def guest_delete_denied(user, project_id):
    """403 ``Response`` when ``user`` is a project Guest (guests may never delete work items), else ``None``."""
    if is_project_guest(user, project_id):
        return Response({"error": GUEST_DELETE_DENIED_MESSAGE}, status=status.HTTP_403_FORBIDDEN)
    return None


GUEST_ARCHIVED_COMMENT_DENIED_MESSAGE = "Guests cannot comment on archived work items"


def guest_archived_comment_denied(user, project_id, issue):
    """403 ``Response`` when a project Guest comments on (or edits a comment of) an archived work item, else ``None``.

    Members and admins are unaffected. ``issue`` is the already loaded work item (``archived_at`` is checked first,
    so a live work item costs no extra query).
    """
    if issue is not None and issue.archived_at is not None and is_project_guest(user, project_id):
        return Response({"error": GUEST_ARCHIVED_COMMENT_DENIED_MESSAGE}, status=status.HTTP_403_FORBIDDEN)
    return None


def audit_created_by(request, slug):
    """Author to record for an imported record: ``created_by`` from the body only for a workspace Admin.

    Everyone else is always recorded as themselves (the body value is ignored, not rejected), so an
    API-key client cannot forge authorship. Import / migration integrations run as a workspace Admin.
    """
    if is_workspace_admin(request.user, slug):
        return request.data.get("created_by", request.user.id)
    return request.user.id


def audit_created_at(request, slug):
    """``created_at`` from the body only for a workspace Admin; otherwise now."""
    if is_workspace_admin(request.user, slug):
        return request.data.get("created_at", timezone.now())
    return timezone.now()
