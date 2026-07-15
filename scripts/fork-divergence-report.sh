#!/usr/bin/env bash
#
# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
#
# Fork-divergence report for the custom-properties feature.
#
# Prints two things so the divergence budget in
# docs/custom-properties-design.md §6 can be audited in CI and after every
# upstream merge:
#   1. the diff (numstat) of upstream-owned paths vs the upstream branch, and
#   2. the `FORK: custom-properties` marker inventory (the precise measure of
#      how many upstream-owned lines this feature touches).
#
# In this clone `origin` IS upstream (makeplane/plane) and `fork` is the team
# remote; override with UPSTREAM_REF if that mapping differs.

set -uo pipefail

UPSTREAM="${UPSTREAM_REF:-origin/preview}"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT" || exit 1

# Upstream-owned paths where any divergence must be justified (P7 metric).
CORE_PATHS=(
  apps/web/core
  apps/api/plane/app
  apps/api/plane/db
  apps/api/plane/settings
  apps/api/plane/urls.py
  packages
)

echo "== Fork divergence vs ${UPSTREAM} (upstream-owned paths) =="
if git rev-parse --verify --quiet "${UPSTREAM}" >/dev/null 2>&1; then
  git diff --numstat "${UPSTREAM}...HEAD" -- "${CORE_PATHS[@]}"
else
  echo "(cannot resolve ${UPSTREAM}; run 'git fetch origin' — skipping diff)"
fi

echo
echo "== FORK: custom-properties markers (budget: <= 40 lines across <= 10 files) =="
markers="$(grep -rn "FORK: custom-properties" apps packages 2>/dev/null | sort)"
if [ -n "$markers" ]; then
  echo "$markers"
fi

lines="$(printf '%s\n' "$markers" | grep -c . )"
files="$(grep -rl "FORK: custom-properties" apps packages 2>/dev/null | wc -l | tr -d ' ')"
echo
echo "TOTAL: ${lines} marked line(s) across ${files} file(s)  (budget: <= 40 lines, <= 10 files)"

if [ "${lines}" -gt 40 ] || [ "${files}" -gt 10 ]; then
  echo "WARNING: over the §6 divergence budget — review before merging."
  exit 2
fi
