# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-properties views.

Phase 0 ships only a capability/health probe. Property/option/value viewsets
are added in Phase 1/2 (see docs/custom-properties-design.md §4).
"""

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from plane.properties.flags import is_instance_enabled


class CustomPropertiesHealthEndpoint(APIView):
    """Report whether the instance kill switch is on. No auth: reveals a single
    boolean capability flag and nothing project-specific."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"instance_enabled": is_instance_enabled()})
