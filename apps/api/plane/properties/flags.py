# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Instance-level kill switch for the custom-properties feature.

The feature is double-gated (docs/custom-properties-design.md §8):
1. this instance env switch (operator-controlled, off by default), and
2. a per-project toggle (ProjectPropertiesFeature.is_enabled).

Reading the env var here keeps settings/common.py free of an extra line.
"""

import os


def is_instance_enabled():
    """True only when the operator has opted the whole instance in.

    Off unless CUSTOM_PROPERTIES_ENABLED is set to "1". When off, every
    plane.properties endpoint refuses and the serializer hook (if built) no-ops.
    """
    return os.environ.get("CUSTOM_PROPERTIES_ENABLED", "0") == "1"
