/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { EIssuePropertyType } from "./types";

export type TPropertyTypeMeta = {
  type: EIssuePropertyType;
  i18n_label: string;
  /** v1 ships Status (OPTION) only; the rest are listed for parity but disabled. */
  enabled: boolean;
};

/**
 * Column-type picker metadata. Status (OPTION) is the only enabled type in v1;
 * the others are shown greyed-out ("soon") so the picker matches upstream's set
 * without implying support that isn't built yet. See design §5.6 / §9 P5.
 */
export const PROPERTY_TYPE_META: TPropertyTypeMeta[] = [
  { type: EIssuePropertyType.OPTION, i18n_label: "custom_properties.type.status", enabled: true },
  { type: EIssuePropertyType.TEXT, i18n_label: "custom_properties.type.text", enabled: false },
  { type: EIssuePropertyType.DECIMAL, i18n_label: "custom_properties.type.number", enabled: false },
  { type: EIssuePropertyType.DATETIME, i18n_label: "custom_properties.type.date", enabled: false },
  { type: EIssuePropertyType.BOOLEAN, i18n_label: "custom_properties.type.checkbox", enabled: false },
  { type: EIssuePropertyType.RELATION, i18n_label: "custom_properties.type.member", enabled: false },
  { type: EIssuePropertyType.URL, i18n_label: "custom_properties.type.url", enabled: false },
  { type: EIssuePropertyType.EMAIL, i18n_label: "custom_properties.type.email", enabled: false },
  { type: EIssuePropertyType.FILE, i18n_label: "custom_properties.type.file", enabled: false },
];

/** Settings sub-route segment (see design A3/A4). */
export const CUSTOM_PROPERTIES_ROUTE = "custom-properties";

/** Bulk value-read cap — the frontend batches ids into calls of this size (design §4.3). */
export const CUSTOM_PROPERTY_VALUES_BATCH_SIZE = 100;
