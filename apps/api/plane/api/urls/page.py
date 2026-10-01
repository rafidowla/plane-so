# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Routes the stock Plane MCP connector's page and workitem_type tools call (FORK: PSR-86)."""

from django.urls import path

from plane.api.views.page import (
    WORK_ITEM_PAGES_UNAVAILABLE,
    WORK_ITEM_TYPES_UNAVAILABLE,
    WORKSPACE_PAGES_UNAVAILABLE,
    NotAvailableAPIEndpoint,
    ProjectPageArchiveAPIEndpoint,
    ProjectPageDetailAPIEndpoint,
    ProjectPageListCreateAPIEndpoint,
)

http_methods = ["get", "post", "put", "patch", "delete"]


def _unavailable(route, message, name):
    """A route that answers every method with HTTP 400 and ``message``.

    Path segments are ``<str:...>`` on purpose: a malformed id must still reach the view and get the
    message, not fall through to a 404.
    """
    return path(
        route,
        NotAvailableAPIEndpoint.as_view(message=message, http_method_names=http_methods),
        name=name,
    )


PROJECT = "workspaces/<str:slug>/projects/<str:project_id>/"

urlpatterns = [
    # Project pages.
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/pages/",
        ProjectPageListCreateAPIEndpoint.as_view(http_method_names=["get", "post"]),
        name="project-pages",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/pages/<uuid:page_id>/",
        ProjectPageDetailAPIEndpoint.as_view(http_method_names=["get", "put", "patch", "delete"]),
        name="project-page-detail",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/pages/<uuid:page_id>/archive/",
        ProjectPageArchiveAPIEndpoint.as_view(http_method_names=["post", "delete"]),
        name="project-page-archive",
    ),
    # Workspace pages (wiki): not on this edition.
    _unavailable("workspaces/<str:slug>/pages/", WORKSPACE_PAGES_UNAVAILABLE, "workspace-pages"),
    _unavailable("workspaces/<str:slug>/pages/<str:page_id>/", WORKSPACE_PAGES_UNAVAILABLE, "workspace-page-detail"),
    _unavailable(
        "workspaces/<str:slug>/pages/<str:page_id>/archive/", WORKSPACE_PAGES_UNAVAILABLE, "workspace-page-archive"
    ),
    # Pages attached to work items: not on this edition.
    _unavailable(
        PROJECT + "work-items/<str:issue_id>/pages/",
        WORK_ITEM_PAGES_UNAVAILABLE,
        "work-item-pages",
    ),
    _unavailable(
        PROJECT + "work-items/<str:issue_id>/pages/<str:pk>/",
        WORK_ITEM_PAGES_UNAVAILABLE,
        "work-item-page-detail",
    ),
    # Work item types / epics: not on this edition.
    _unavailable(PROJECT + "work-item-types/", WORK_ITEM_TYPES_UNAVAILABLE, "project-work-item-types"),
    _unavailable(
        PROJECT + "work-item-types/<str:type_id>/",
        WORK_ITEM_TYPES_UNAVAILABLE,
        "project-work-item-type-detail",
    ),
    _unavailable(
        PROJECT + "import-work-item-types/",
        WORK_ITEM_TYPES_UNAVAILABLE,
        "project-import-work-item-types",
    ),
    _unavailable("workspaces/<str:slug>/work-item-types/", WORK_ITEM_TYPES_UNAVAILABLE, "workspace-work-item-types"),
    _unavailable(
        "workspaces/<str:slug>/work-item-types/<str:type_id>/",
        WORK_ITEM_TYPES_UNAVAILABLE,
        "workspace-work-item-type-detail",
    ),
]
