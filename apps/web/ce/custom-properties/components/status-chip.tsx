/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// plane imports
import { cn } from "@plane/utils";
// local imports
import type { TIssuePropertyOption } from "@/plane-web/custom-properties";
import { EMPTY_OPTION_BG, textColorFor } from "../utils/contrast";

type Props = {
  option: TIssuePropertyOption;
  className?: string;
};

/** Small filled pill for a selected OPTION value — used on cards and in inputs. */
export function StatusChip({ option, className }: Props) {
  const bg = option.logo_props?.color?.background ?? EMPTY_OPTION_BG;
  return (
    <span
      className={cn("inline-flex max-w-full items-center truncate rounded px-1.5 py-0.5 text-[11px] font-medium", className)}
      style={{ backgroundColor: bg, color: textColorFor(bg) }}
    >
      {option.name}
    </span>
  );
}
