# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Project pages for the public API, plus "not available on this edition" answers (FORK: PSR-86).

Serve the stock Plane MCP connector's ``page`` tool, which targets the commercial API shape:

- ``workspaces/<slug>/projects/<project_id>/pages/`` (GET list, POST create)
- ``.../pages/<page_id>/`` (GET retrieve, PUT/PATCH update -- the SDK sends PUT, both are partial --
  and DELETE)
- ``.../pages/<page_id>/archive/`` (POST archive, DELETE restore)

Visibility and business rules mirror the app's ``PageViewSet`` / ``PagesDescriptionViewSet``
(``plane/app/views/page/base.py``):

- A page is reachable only through a live ``ProjectPage`` link to the project in the URL, in a project
  that is not archived. The caller must be an active project member (``ProjectEntityPermission``; guests
  are read-only).
- A page is visible when it is public or owned by the caller. A guest in a project with
  ``guest_view_all_features`` off sees only pages they own. Anything else answers 404 -- never 403 --
  so a private page's existence is not disclosed. This covers every method, so only the owner can
  change a private page.
- Only the owner may change ``access``; only the owner or a project admin may archive, restore or
  delete; delete requires the page to be archived first; archive/restore cascade to descendants.
- Unlike the app's list, child pages are listed too (each row carries ``parent_id``): the connector
  builds hierarchies from the list and has no "children of" call. Archived pages are left out unless
  ``?archived=true`` is passed.

Editor storage. A page keeps ``description_html`` next to ``description_binary``, the Yjs document the
live editor works on. The live server rebuilds the binary from the html whenever the binary is empty
(``apps/live/src/extensions/database.ts``), so this module never calls the live server:

- create stores the sanitised html and leaves the binary empty.
- an update that changes the html stores the new html, resets ``description_binary`` to empty and
  ``description_json`` to the model default so the editor regenerates from the html on next open, and
  records ``PageVersion`` rows: one for the outgoing content if the latest version does not already
  hold it (so it stays restorable), and one for the new content. Versions are written here, in the
  request, rather than through ``track_page_version``: each API update is a deliberate whole-body
  replacement, so it is never folded into a recent version the way editor autosaves are.

Not available on this edition (workspace pages, work-item pages, work item types / epics): every
method answers HTTP 400 ``{"error": <what to do instead>}`` so the connector surfaces one clear message
instead of a 404.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response

from plane.api.views.base import BaseAPIView
from plane.app.permissions import ROLE, ProjectEntityPermission
from plane.app.views.page.base import unarchive_archive_page_and_descendants
from plane.bgtasks.page_transaction_task import page_transaction
from plane.db.models import Page, PageVersion, Project, ProjectMember, ProjectPage, UserFavorite, UserRecentVisit
from plane.utils.content_validator import validate_html_content
from plane.utils.openapi import (
    CURSOR_PARAMETER,
    DELETED_RESPONSE,
    FORBIDDEN_RESPONSE,
    INVALID_REQUEST_RESPONSE,
    NOT_FOUND_RESPONSE,
    ORDER_BY_PARAMETER,
    PER_PAGE_PARAMETER,
    PROJECT_ID_PARAMETER,
    UNAUTHORIZED_RESPONSE,
    WORKSPACE_SLUG_PARAMETER,
    create_paginated_response,
)
from plane.utils.order_queryset import PAGE_ORDER_BY_ALLOWLIST, sanitize_order_by

EMPTY_HTML = "<p></p>"
MAX_PAGE_VERSIONS = 20

WORKSPACE_PAGES_UNAVAILABLE = (
    "Workspace-level pages (wiki) are not available on this Plane edition. Pass project_id to work with a "
    "project's pages."
)
WORK_ITEM_PAGES_UNAVAILABLE = (
    "Attaching pages to work items is not available on this Plane edition. Add the page URL as a work item "
    "link instead (workitem_link create)."
)
WORK_ITEM_TYPES_UNAVAILABLE = (
    "Work item types and epics are not available on this Plane edition. To model an epic, create a normal "
    "work item, add an 'Epic' label, set parent=<its id> on the child work items, and list children with "
    'pql childOf("<IDENTIFIER>").'
)
COLLECTIONS_UNAVAILABLE = "Page collections are not available on this Plane edition. Remove: {fields}."
ARCHIVED_AT_READ_ONLY = "archived_at cannot be set directly. Use the page's archive endpoint (page archive)."
PARENT_FIXED = "A page's parent is fixed at creation and cannot be changed afterwards."

PAGE_NOT_FOUND = "Page not found"
PAGE_LOCKED = "This page is locked and cannot be updated. Unlock it first."
PAGE_ARCHIVED = "This page is archived and cannot be updated. Restore it first (page archive with archive=false)."
ACCESS_OWNER_ONLY = "Access cannot be updated since this page is owned by someone else"
ARCHIVE_OWNER_OR_ADMIN = "Only the owner or admin can archive the page"
UNARCHIVE_OWNER_OR_ADMIN = "Only the owner or admin can un archive the page"
DELETE_NEEDS_ARCHIVE = "The page should be archived before deleting"
DELETE_OWNER_OR_ADMIN = "Only admin or owner can delete the page"

PAID_ONLY_FIELDS = ("collection_id", "collection")
COMMON_FIELDS = (
    "name",
    "description_html",
    "access",
    "color",
    "is_locked",
    "external_id",
    "external_source",
    "view_props",
    "logo_props",
)
PARENT_FIELDS = ("parent_id", "parent")

PAGE_ID_PARAMETER = OpenApiParameter(
    name="page_id",
    description="Page ID",
    required=True,
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.PATH,
)
ARCHIVED_PARAMETER = OpenApiParameter(
    name="archived",
    description="Pass true to list archived pages instead of live ones",
    required=False,
    type=OpenApiTypes.BOOL,
    location=OpenApiParameter.QUERY,
)


def _error(message, code=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({"error": message, **extra}, status=code)


class PageLiteAPISerializer(serializers.ModelSerializer):
    """A page without its body: what the list returns."""

    parent_id = serializers.UUIDField(read_only=True, allow_null=True)
    projects = serializers.SerializerMethodField()

    class Meta:
        model = Page
        fields = [
            "id",
            "name",
            "owned_by",
            "access",
            "color",
            "is_locked",
            "archived_at",
            "parent_id",
            "workspace",
            "projects",
            "view_props",
            "logo_props",
            "external_id",
            "external_source",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
        ]
        read_only_fields = fields

    def get_projects(self, obj) -> list[str]:
        # Scoped to the project in the URL: other projects a page may be linked to are not the
        # caller's business here (they may not be a member of them).
        return [str(self.context["project_id"])]


class PageAPISerializer(PageLiteAPISerializer):
    """A page with its body: what retrieve, create and update return."""

    class Meta(PageLiteAPISerializer.Meta):
        fields = PageLiteAPISerializer.Meta.fields + ["description_html", "description_stripped"]
        read_only_fields = fields


class PageWriteSerializer(serializers.Serializer):
    """Validates the writable fields. ``name`` is required on create only (see ``partial``)."""

    name = serializers.CharField(trim_whitespace=True)
    description_html = serializers.CharField(required=False, allow_blank=True)
    access = serializers.ChoiceField(choices=[Page.PUBLIC_ACCESS, Page.PRIVATE_ACCESS], required=False)
    color = serializers.CharField(required=False, allow_blank=True, max_length=255)
    is_locked = serializers.BooleanField(required=False)
    external_id = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=255)
    external_source = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=255)
    view_props = serializers.DictField(required=False)
    logo_props = serializers.DictField(required=False)
    parent_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_description_html(self, value):
        """Sanitise with the same validator the app uses for page content."""
        if not value:
            return EMPTY_HTML
        is_valid, error_message, sanitized_html = validate_html_content(value)
        if not is_valid:
            raise serializers.ValidationError(error_message)
        return sanitized_html if sanitized_html is not None else value


def _body_error(data, allowed):
    """A 400 for a body this edition cannot honour, or None. Nothing is ever silently dropped."""
    if not isinstance(data, dict):
        return _error("Request body must be a JSON object.")
    paid = sorted(field for field in PAID_ONLY_FIELDS if field in data)
    if paid:
        return _error(COLLECTIONS_UNAVAILABLE.format(fields=", ".join(paid)))
    if "archived_at" in data:
        return _error(ARCHIVED_AT_READ_ONLY)
    unknown = sorted(str(field) for field in data if field not in allowed)
    if unknown:
        return _error(f"Unknown or unsupported field(s): {', '.join(unknown)}.")
    return None


def _visible_pages(request, slug, project_id):
    """Pages of the project the caller may see -- the single gate every page endpoint goes through."""
    queryset = Page.objects.filter(
        workspace__slug=slug,
        project_pages__project_id=project_id,
        project_pages__deleted_at__isnull=True,
        project_pages__project__archived_at__isnull=True,
    ).filter(Q(owned_by=request.user) | Q(access=Page.PUBLIC_ACCESS))
    if ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=request.user,
        role=ROLE.GUEST.value,
        is_active=True,
        project__guest_view_all_features=False,
    ).exists():
        queryset = queryset.filter(owned_by=request.user)
    return queryset


def _is_project_admin(request, slug, project_id) -> bool:
    return ProjectMember.objects.filter(
        workspace__slug=slug,
        project_id=project_id,
        member=request.user,
        role=ROLE.ADMIN.value,
        is_active=True,
    ).exists()


def _external_conflict(slug, project_id, external_id, external_source, exclude_id=None):
    """The 409 the public API gives for a duplicate external id/source pair, or None."""
    if not external_id or not external_source:
        return None
    duplicate = Page.objects.filter(
        workspace__slug=slug,
        project_pages__project_id=project_id,
        project_pages__deleted_at__isnull=True,
        external_id=external_id,
        external_source=external_source,
    )
    if exclude_id:
        duplicate = duplicate.exclude(pk=exclude_id)
    duplicate = duplicate.first()
    if duplicate is None:
        return None
    return _error(
        "Page with the same external id and external source already exists",
        status.HTTP_409_CONFLICT,
        id=str(duplicate.id),
    )


def _record_versions(page, previous, user_id):
    """Snapshot the outgoing content (unless the latest version already holds it), then the new content."""
    latest = PageVersion.objects.filter(page_id=page.id).order_by("-last_saved_at", "-created_at").first()
    if latest is None or latest.description_html != previous["description_html"]:
        PageVersion(
            page_id=page.id,
            workspace_id=page.workspace_id,
            owned_by_id=previous["updated_by_id"] or page.owned_by_id,
            last_saved_at=previous["updated_at"] or timezone.now(),
            description_html=previous["description_html"],
            description_binary=previous["description_binary"],
            description_json=previous["description_json"],
        ).save(created_by_id=user_id)
    PageVersion(
        page_id=page.id,
        workspace_id=page.workspace_id,
        owned_by_id=user_id,
        last_saved_at=timezone.now(),
        description_html=page.description_html,
        description_binary=None,
        description_json=page.description_json,
    ).save(created_by_id=user_id)
    stale = list(
        PageVersion.objects.filter(page_id=page.id)
        .order_by("-last_saved_at", "-created_at")
        .values_list("id", flat=True)[MAX_PAGE_VERSIONS:]
    )
    if stale:
        PageVersion.objects.filter(id__in=stale).delete()


class ProjectPageListCreateAPIEndpoint(BaseAPIView):
    """List the project's pages the caller can see, or create one."""

    permission_classes = [ProjectEntityPermission]
    serializer_class = PageAPISerializer

    @extend_schema(
        operation_id="list_project_pages",
        tags=["Pages"],
        summary="List project pages",
        description=(
            "Paginated list of the project's pages the caller can see: public pages and the caller's own "
            "private ones. Rows carry no body; retrieve a page for its description_html."
        ),
        parameters=[
            WORKSPACE_SLUG_PARAMETER,
            PROJECT_ID_PARAMETER,
            CURSOR_PARAMETER,
            PER_PAGE_PARAMETER,
            ORDER_BY_PARAMETER,
            ARCHIVED_PARAMETER,
        ],
        responses={
            200: create_paginated_response(
                PageLiteAPISerializer,
                "PaginatedProjectPageResponse",
                "Paginated list of project pages",
                "Paginated Project Pages",
            ),
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
        },
    )
    def get(self, request, slug, project_id):
        archived = request.GET.get("archived", "false").lower() == "true"
        queryset = (
            _visible_pages(request, slug, project_id)
            .filter(archived_at__isnull=not archived)
            .order_by(
                sanitize_order_by(
                    request.GET.get("order_by", "-created_at"), PAGE_ORDER_BY_ALLOWLIST, default="-created_at"
                ),
                "id",
            )
        )
        return self.paginate(
            request=request,
            queryset=queryset,
            on_results=lambda pages: PageLiteAPISerializer(pages, many=True, context={"project_id": project_id}).data,
        )

    @extend_schema(
        operation_id="create_project_page",
        tags=["Pages"],
        summary="Create project page",
        description=(
            "Create a page in the project, owned by the caller. description_html is sanitised; the editor "
            "builds its document from it the first time the page is opened."
        ),
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER],
        request=PageWriteSerializer,
        responses={
            201: PageAPISerializer,
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            409: OpenApiResponse(description="A page with the same external id and source already exists"),
        },
    )
    def post(self, request, slug, project_id):
        data = request.data
        if error := _body_error(data, COMMON_FIELDS + PARENT_FIELDS):
            return error

        # The SDK sends parent_id; the app's own name for it is parent.
        payload = {key: value for key, value in data.items() if key != "parent"}
        if "parent" in data and "parent_id" not in data:
            payload["parent_id"] = data["parent"]
        serializer = PageWriteSerializer(data=payload)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        fields = dict(serializer.validated_data)

        parent_id = fields.pop("parent_id", None)
        if (
            parent_id
            and not _visible_pages(request, slug, project_id).filter(pk=parent_id, archived_at__isnull=True).exists()
        ):
            return _error("Parent page not found in this project, or it is archived.")

        if error := _external_conflict(slug, project_id, fields.get("external_id"), fields.get("external_source")):
            return error

        project = Project.objects.get(pk=project_id, workspace__slug=slug)
        fields.setdefault("description_html", EMPTY_HTML)
        with transaction.atomic():
            page = Page(
                **fields,
                parent_id=parent_id,
                workspace_id=project.workspace_id,
                owned_by=request.user,
                description_json={},
                description_binary=None,
            )
            page.save(created_by_id=request.user.id)
            ProjectPage(workspace_id=project.workspace_id, project_id=project.id, page_id=page.id).save(
                created_by_id=request.user.id
            )

        page_transaction.delay(
            new_description_html=page.description_html,
            old_description_html=None,
            page_id=str(page.id),
        )
        return Response(
            PageAPISerializer(page, context={"project_id": project_id}).data,
            status=status.HTTP_201_CREATED,
        )


class ProjectPageDetailAPIEndpoint(BaseAPIView):
    """Retrieve, update or delete one project page."""

    permission_classes = [ProjectEntityPermission]
    serializer_class = PageAPISerializer

    @extend_schema(
        operation_id="retrieve_project_page",
        tags=["Pages"],
        summary="Retrieve project page",
        description="Retrieve a page with its body (description_html).",
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, PAGE_ID_PARAMETER],
        responses={
            200: PageAPISerializer,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    )
    def get(self, request, slug, project_id, page_id):
        page = _visible_pages(request, slug, project_id).filter(pk=page_id).first()
        if page is None:
            return _error(PAGE_NOT_FOUND, status.HTTP_404_NOT_FOUND)
        return Response(PageAPISerializer(page, context={"project_id": project_id}).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="update_project_page",
        tags=["Pages"],
        summary="Update project page",
        description=(
            "Change only the fields sent. A new description_html replaces the whole body, resets the "
            "editor's stored document so it is rebuilt from the html, and records page versions so the "
            "previous body stays restorable. A locked or archived page is refused."
        ),
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, PAGE_ID_PARAMETER],
        request=PageWriteSerializer,
        responses={
            200: PageAPISerializer,
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
            409: OpenApiResponse(description="A page with the same external id and source already exists"),
        },
    )
    def patch(self, request, slug, project_id, page_id):
        page = _visible_pages(request, slug, project_id).filter(pk=page_id).first()
        if page is None:
            return _error(PAGE_NOT_FOUND, status.HTTP_404_NOT_FOUND)

        data = request.data
        if isinstance(data, dict) and any(field in data for field in PARENT_FIELDS):
            return _error(PARENT_FIXED)
        if error := _body_error(data, COMMON_FIELDS):
            return error
        serializer = PageWriteSerializer(data=data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        fields = dict(serializer.validated_data)
        if not fields:
            return _error("Nothing to update. Pass at least one field, e.g. name or description_html.")

        if page.archived_at:
            return _error(PAGE_ARCHIVED)
        # A locked page takes no edits; the one thing an update may do to it is unlock it.
        if page.is_locked and fields.get("is_locked") is not False:
            return _error(PAGE_LOCKED)
        if fields.get("access", page.access) != page.access and page.owned_by_id != request.user.id:
            return _error(ACCESS_OWNER_ONLY)

        external_id = fields.get("external_id", page.external_id)
        external_source = fields.get("external_source", page.external_source)
        if (external_id, external_source) != (page.external_id, page.external_source):
            if error := _external_conflict(slug, project_id, external_id, external_source, exclude_id=page.id):
                return error

        previous = {
            "description_html": page.description_html,
            "description_binary": page.description_binary,
            "description_json": page.description_json,
            "updated_at": page.updated_at,
            "updated_by_id": page.updated_by_id,
        }
        html_changed = "description_html" in fields and fields["description_html"] != page.description_html

        with transaction.atomic():
            for field, value in fields.items():
                setattr(page, field, value)
            if html_changed:
                # Empty binary = "rebuild from description_html" for the live editor.
                page.description_binary = None
                page.description_json = {}
            page.updated_by = request.user
            page.save(disable_auto_set_user=True)
            if html_changed:
                _record_versions(page, previous, request.user.id)

        if html_changed:
            page_transaction.delay(
                new_description_html=page.description_html,
                old_description_html=previous["description_html"],
                page_id=str(page.id),
            )
        return Response(PageAPISerializer(page, context={"project_id": project_id}).data, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="replace_project_page",
        tags=["Pages"],
        summary="Update project page (PUT)",
        description="Same as PATCH: only the fields sent are changed. Kept because the Plane SDK sends PUT.",
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, PAGE_ID_PARAMETER],
        request=PageWriteSerializer,
        responses={
            200: PageAPISerializer,
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    )
    def put(self, request, slug, project_id, page_id):
        return self.patch(request, slug, project_id, page_id)

    @extend_schema(
        operation_id="delete_project_page",
        tags=["Pages"],
        summary="Delete project page",
        description="Delete an archived page. Only its owner or a project admin may delete it.",
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, PAGE_ID_PARAMETER],
        responses={
            204: DELETED_RESPONSE,
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    )
    def delete(self, request, slug, project_id, page_id):
        page = _visible_pages(request, slug, project_id).filter(pk=page_id).first()
        if page is None:
            return _error(PAGE_NOT_FOUND, status.HTTP_404_NOT_FOUND)
        if page.archived_at is None:
            return _error(DELETE_NEEDS_ARCHIVE)
        if page.owned_by_id != request.user.id and not _is_project_admin(request, slug, project_id):
            return _error(DELETE_OWNER_OR_ADMIN, status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            # Children outlive their parent, as in the app.
            Page.objects.filter(
                parent_id=page_id,
                workspace__slug=slug,
                project_pages__project_id=project_id,
                project_pages__deleted_at__isnull=True,
            ).update(parent=None)
            page.delete()
            UserFavorite.objects.filter(
                project_id=project_id,
                workspace__slug=slug,
                entity_identifier=page_id,
                entity_type="page",
            ).delete()
            UserRecentVisit.objects.filter(
                project_id=project_id,
                workspace__slug=slug,
                entity_identifier=page_id,
                entity_name="page",
            ).delete(soft=False)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectPageArchiveAPIEndpoint(BaseAPIView):
    """Archive (POST) or restore (DELETE) a page together with its descendants."""

    permission_classes = [ProjectEntityPermission]
    serializer_class = PageAPISerializer

    def _page_or_error(self, request, slug, project_id, page_id, refusal):
        page = _visible_pages(request, slug, project_id).filter(pk=page_id).first()
        if page is None:
            return None, _error(PAGE_NOT_FOUND, status.HTTP_404_NOT_FOUND)
        if page.owned_by_id != request.user.id and not _is_project_admin(request, slug, project_id):
            return None, _error(refusal)
        return page, None

    @extend_schema(
        operation_id="archive_project_page",
        tags=["Pages"],
        summary="Archive project page",
        description="Archive a page and its descendants. Only its owner or a project admin may archive it.",
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, PAGE_ID_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(description="The page id and its archived_at date"),
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    )
    def post(self, request, slug, project_id, page_id):
        page, error = self._page_or_error(request, slug, project_id, page_id, ARCHIVE_OWNER_OR_ADMIN)
        if error:
            return error
        archived_at = page.archived_at
        if archived_at is None:
            archived_at = timezone.now().date()
            with transaction.atomic():
                UserFavorite.objects.filter(
                    entity_type="page",
                    entity_identifier=page_id,
                    project_id=project_id,
                    workspace__slug=slug,
                ).delete()
                unarchive_archive_page_and_descendants(page_id, archived_at)
        return Response({"id": str(page.id), "archived_at": str(archived_at)}, status=status.HTTP_200_OK)

    @extend_schema(
        operation_id="unarchive_project_page",
        tags=["Pages"],
        summary="Restore project page",
        description=(
            "Restore an archived page and its descendants. If its parent is still archived the page is "
            "detached from it. Only its owner or a project admin may restore it."
        ),
        parameters=[WORKSPACE_SLUG_PARAMETER, PROJECT_ID_PARAMETER, PAGE_ID_PARAMETER],
        responses={
            204: OpenApiResponse(description="Page restored"),
            400: INVALID_REQUEST_RESPONSE,
            401: UNAUTHORIZED_RESPONSE,
            403: FORBIDDEN_RESPONSE,
            404: NOT_FOUND_RESPONSE,
        },
    )
    def delete(self, request, slug, project_id, page_id):
        page, error = self._page_or_error(request, slug, project_id, page_id, UNARCHIVE_OWNER_OR_ADMIN)
        if error:
            return error
        with transaction.atomic():
            # Restoring under a parent that stays archived would hide the page again.
            if page.parent_id and page.parent.archived_at:
                Page.objects.filter(pk=page.id).update(parent=None)
            unarchive_archive_page_and_descendants(page_id, None)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(exclude=True)
class NotAvailableAPIEndpoint(BaseAPIView):
    """Answers every method with HTTP 400 and ``message``: the feature does not exist on this edition.

    400 rather than 404 so the connector shows the agent the message (what to do instead) rather than
    a bare "not found". Authentication still applies; nothing is read or written.
    """

    message = ""

    def _refuse(self, request, *args, **kwargs):
        return _error(self.message)

    get = post = put = patch = delete = _refuse
