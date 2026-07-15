# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Custom-properties data model.

Models are added in Phase 1 (see docs/custom-properties-design.md §3):
IssueProperty, IssuePropertyOption, IssuePropertyValue, ProjectPropertiesFeature.
Table names mirror upstream Plane's work-item-properties schema so the fork can
adopt an upstream implementation later instead of rewriting data.
"""
