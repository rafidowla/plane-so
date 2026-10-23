# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# FORK: upstream sync 2026-10-23 — joins upstream's 0125_reset_project_deploy_board_identity
# (makeplane/plane 490619341f) with the fork's chain (0134_time_holidays_leave). No operations.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0125_reset_project_deploy_board_identity"),
        ("db", "0134_time_holidays_leave"),
    ]

    operations = []
