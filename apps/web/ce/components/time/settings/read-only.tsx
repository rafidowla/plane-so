/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: PSR-125 — plain workspace Admins can read the Time settings but only a Super Admin can change
// them. The Settings tab sets this once; every section reads it to hide or disable its write controls.
import { createContext, useContext } from "react";

export const TimeSettingsReadOnlyContext = createContext(false);

export const useTimeSettingsReadOnly = () => useContext(TimeSettingsReadOnlyContext);
