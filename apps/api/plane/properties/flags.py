# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Instance-level kill switch for the custom-properties feature.

The feature is double-gated (docs/custom-properties-design.md §8):
1. this instance env switch (on by default; operator can opt out), and
2. a per-project toggle (ProjectPropertiesFeature.is_enabled), which a
   project admin still has to turn on for their own project.

Reading the env var here keeps settings/common.py free of an extra line.
"""

import os


def is_instance_enabled():
    """True unless the operator has explicitly opted the whole instance out.

    On by default (new deployments need no env var). Set
    CUSTOM_PROPERTIES_ENABLED=0 (or any value other than "1") to disable —
    every plane.properties endpoint then refuses and the serializer hook (if
    built) no-ops. Same idiom as SKIP_ENV_VAR in settings/common.py: default
    "1", strict equality, so a typo in the override disables rather than
    silently staying on.
    """
    return os.environ.get("CUSTOM_PROPERTIES_ENABLED", "1") == "1"
