/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */
// FORK: time-tracking-v2 S5 — the summary tiles at the top of the Reports tab.
import { cn } from "@plane/utils";
import { balanceTone, formatAmount, formatHm, formatHours, formatSignedHours } from "./report-utils";
import { offHint } from "../settings/holidays-utils";
import type { TReportTiles } from "./types";

type TTileProps = {
  id: string;
  label: string;
  value: string;
  hint?: string;
  /** FORK: holidays/leave (PSR-122) — a second muted line, e.g. "incl. 8.0 h off" */
  note?: string | null;
  tone?: "positive" | "negative" | "neutral";
};

function Tile({ id, label, value, hint, note, tone = "neutral" }: TTileProps) {
  return (
    <div className="flex min-w-[140px] flex-1 flex-col gap-1 rounded-lg border border-subtle bg-surface-1 px-4 py-3">
      <span className="text-xs text-tertiary">{label}</span>
      <span
        data-testid={`tile-${id}`}
        className={cn("text-20 font-semibold text-primary", {
          "text-success-primary": tone === "positive",
          "text-danger-primary": tone === "negative",
        })}
      >
        {value}
      </span>
      {hint && <span className="text-xs text-tertiary">{hint}</span>}
      {note && (
        <span data-testid={`tile-${id}-note`} className="text-xs text-tertiary">
          {note}
        </span>
      )}
    </div>
  );
}

type TProps = { tiles: TReportTiles; billingEnabled: boolean; showMembers: boolean };

export function ReportTiles({ tiles, billingEnabled, showMembers }: TProps) {
  const tone = balanceTone(tiles.balance_to_date_minutes);
  const hasBilling = billingEnabled && tiles.billable_minutes !== undefined;
  return (
    <div className="flex flex-col gap-3" data-testid="report-tiles">
      <div className="flex flex-wrap gap-3">
        <Tile
          id="logged"
          label="Logged"
          value={formatHours(tiles.logged_minutes)}
          hint={`${tiles.entry_count} entries`}
        />
        <Tile
          id="expected"
          label="Expected"
          value={formatHours(tiles.expected_minutes)}
          hint="Whole range"
          note={offHint(tiles.off_minutes)}
        />
        <Tile
          id="expected-to-date"
          label="Expected to date"
          value={formatHours(tiles.expected_to_date_minutes)}
          hint="Up to today"
        />
        <Tile
          id="balance"
          label="Balance"
          value={formatSignedHours(tiles.balance_to_date_minutes)}
          hint="Logged minus expected to date"
          tone={tone}
        />
        <Tile id="weekend" label="Weekend" value={formatHours(tiles.weekend_minutes)} hint="Outside the work week" />
        {tiles.running_minutes > 0 && (
          <Tile id="running" label="Running now" value={formatHm(tiles.running_minutes)} hint="Not yet saved" />
        )}
        {showMembers && <Tile id="members" label="People" value={String(tiles.member_count)} />}
      </div>
      {hasBilling && (
        <div className="flex flex-wrap gap-3" data-testid="report-billing-tiles">
          <Tile id="billable" label="Billable" value={formatHours(tiles.billable_minutes)} />
          <Tile id="non-billable" label="Non-billable" value={formatHours(tiles.non_billable_minutes)} />
          <Tile id="amount" label="Billable amount" value={formatAmount(tiles.billable_amount)} />
        </div>
      )}
    </div>
  );
}
