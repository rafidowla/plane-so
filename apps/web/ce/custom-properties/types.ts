/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Custom-properties frontend types.
 *
 * Mirrors the `plane.properties` backend contract (and upstream Plane's
 * work-item-property shape). String enum values match the API payloads exactly.
 * See docs/custom-properties-design.md §3/§5.
 */

export enum EIssuePropertyType {
  TEXT = "TEXT",
  DATETIME = "DATETIME",
  DECIMAL = "DECIMAL",
  BOOLEAN = "BOOLEAN",
  OPTION = "OPTION",
  RELATION = "RELATION",
  URL = "URL",
  EMAIL = "EMAIL",
  FILE = "FILE",
  FORMULA = "FORMULA",
}

export enum EIssuePropertyRelationType {
  ISSUE = "ISSUE",
  USER = "USER",
}

/** Option color lives in `logo_props` (no dedicated column) — see design §3.3. */
export type TPropertyColor = {
  name?: string;
  background: string;
};

export type TPropertyLogoProps = {
  in_use?: string;
  color?: TPropertyColor;
};

export type TIssuePropertyOption = {
  id: string;
  property: string;
  name: string;
  description?: string;
  logo_props?: TPropertyLogoProps;
  sort_order: number;
  is_active: boolean;
  is_default: boolean;
  parent?: string | null;
};

export type TIssueProperty = {
  id: string;
  workspace: string;
  project: string;
  issue_type?: string | null;
  name: string;
  display_name: string;
  description?: string;
  property_type: EIssuePropertyType;
  relation_type?: EIssuePropertyRelationType | null;
  is_required: boolean;
  is_active: boolean;
  is_multi: boolean;
  default_value: string[];
  settings: Record<string, unknown>;
  validation_rules: Record<string, unknown>;
  logo_props: Record<string, unknown>;
  sort_order: number;
  options?: TIssuePropertyOption[];
};

/** issue_id -> property_id -> array of scalar values (option ids for OPTION). */
export type TPropertyValuesMap = Record<string, Record<string, string[]>>;

export type TProjectPropertiesFeature = {
  is_enabled: boolean;
  instance_enabled?: boolean;
};
