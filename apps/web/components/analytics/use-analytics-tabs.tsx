/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import { useTranslation } from "@plane/i18n";
import { getAnalyticsTabs } from "./tabs";

// FORK: time-tracking-v2 S5 — `showTimesheets` is true only while weekly approval is on.
export const useAnalyticsTabs = (_workspaceSlug: string, showTimesheets = false) => {
  const { t } = useTranslation();

  const analyticsTabs = useMemo(() => getAnalyticsTabs(t, { showTimesheets }), [t, showTimesheets]);

  return analyticsTabs;
};
