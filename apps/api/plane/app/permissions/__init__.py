# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from .workspace import (
    WorkSpaceBasePermission,
    WorkspaceOwnerPermission,
    WorkSpaceAdminPermission,
    WorkspaceEntityPermission,
    WorkspaceViewerPermission,
    WorkspaceUserPermission,
    WorkspaceMemberPermission,
)
from .project import (
    ProjectBasePermission,
    ProjectArchiveUnarchivePermission,
    ProjectEntityPermission,
    ProjectMemberPermission,
    ProjectLitePermission,
    ProjectAdminPermission,
)
from .base import (
    allow_permission,
    issue_hidden_from_guest,
    ROLE,
    # FORK: PSR-125
    SUPER_ADMIN_REQUIRED_ERROR,
    allow_workspace_super_admin,
    is_workspace_super_admin,
    super_admin_forbidden,
)
from .page import ProjectPagePermission
