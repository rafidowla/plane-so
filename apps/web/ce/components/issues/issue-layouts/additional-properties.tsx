/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React from "react";
import type { IIssueDisplayProperties, TIssue } from "@plane/types";
// FORK: custom-properties
import { CustomPropertiesCardChips } from "@/plane-web/custom-properties";

export type TWorkItemLayoutAdditionalProperties = {
  displayProperties: IIssueDisplayProperties;
  issue: TIssue;
};

export function WorkItemLayoutAdditionalProperties(props: TWorkItemLayoutAdditionalProperties) {
  return <CustomPropertiesCardChips issue={props.issue} />; // FORK: custom-properties
}
