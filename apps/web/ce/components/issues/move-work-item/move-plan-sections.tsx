/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: move-work-item (PSR-91) — presentational sections of the "Move to project" dialog
import type { ReactNode } from "react";
import { Banner } from "@makeplane/propel/components/banner";
import { Radio, RadioGroup } from "@makeplane/propel/components/radio";
import { Select } from "@plane/blocks/select";
import { useTranslation } from "@plane/i18n";
import type { TMovePlan, TMoveState, TMoveSubItemsMode } from "@plane/utils";
import {
  formatMoveDuration,
  getSelectedTargetStateId,
  hasWorklogWarnings,
  shouldShowSubItemsChoice,
  summarizeMoveDrops,
} from "@plane/utils";

export const MoveSection = ({ title, children }: { title: string; children: ReactNode }) => (
  <section className="space-y-2">
    <h4 className="text-body-xs-semibold text-primary">{title}</h4>
    {children}
  </section>
);

const StateDot = ({ color }: { color: string }) => (
  <span className="size-2 flex-shrink-0 rounded-full" style={{ backgroundColor: color }} aria-hidden="true" />
);

type TStateMappingProps = {
  plan: TMovePlan;
  overrides: Record<string, string | undefined>;
  onOverride: (sourceStateId: string, targetStateId: string) => void;
  disabled?: boolean;
};

export const MoveStateMapping = ({ plan, overrides, onOverride, disabled }: TStateMappingProps) => {
  const { t } = useTranslation();
  if (plan.states.length === 0) return null;
  return (
    <MoveSection title={t("move_work_item.sections.states")}>
      <p className="text-body-xs-regular text-tertiary">
        {t("move_work_item.states_description", { project: plan.target_project.name })}
      </p>
      <ul className="space-y-1.5">
        {plan.states.map((row) => {
          const selectedId = getSelectedTargetStateId(row, plan.target_states, overrides);
          const selected = plan.target_states.find((state) => state.id === selectedId) ?? null;
          const note =
            selectedId && overrides[row.source.id] === selectedId
              ? null
              : row.match === "group"
                ? t("move_work_item.state_match.group")
                : row.match === "default"
                  ? t("move_work_item.state_match.default")
                  : row.match === "none" && !selected
                    ? t("move_work_item.state_match.none")
                    : null;
          return (
            <li key={row.source.id} className="flex items-center gap-2">
              <span className="flex min-w-0 basis-2/5 items-center gap-2 text-body-xs-medium text-secondary">
                <StateDot color={row.source.color} />
                <span className="truncate">{row.source.name}</span>
                {row.work_item_ids.length > 1 && (
                  <span className="flex-shrink-0 text-tertiary">
                    {t("move_work_item.work_items_count", { count: row.work_item_ids.length })}
                  </span>
                )}
              </span>
              <span className="text-tertiary" aria-hidden="true">
                →
              </span>
              <span className="min-w-0 flex-1">
                <Select<TMoveState>
                  getValues={() => plan.target_states}
                  value={selected}
                  onChange={(id) => onOverride(row.source.id, id)}
                  getOptionValue={(state) => state.id}
                  getOptionLabel={(state) => state.name}
                  getOptionIcon={(state) => <StateDot color={state.color} />}
                  showSearch={false}
                  pinSelected={false}
                  disabled={disabled}
                >
                  <Select.Trigger variant="select-md" className="w-full">
                    <span className="flex min-w-0 grow items-center gap-2 text-left">
                      {selected ? (
                        <>
                          <StateDot color={selected.color} />
                          <span className="truncate">{selected.name}</span>
                        </>
                      ) : (
                        <span className="truncate text-placeholder">{t("move_work_item.state_choose")}</span>
                      )}
                    </span>
                  </Select.Trigger>
                </Select>
                {note && <span className="mt-0.5 block text-caption-sm-regular text-tertiary">{note}</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </MoveSection>
  );
};

type TSubItemsProps = {
  plan: TMovePlan;
  value: TMoveSubItemsMode;
  onChange: (value: TMoveSubItemsMode) => void;
  disabled?: boolean;
};

export const MoveSubItemsChoice = ({ plan, value, onChange, disabled }: TSubItemsProps) => {
  const { t } = useTranslation();
  if (!shouldShowSubItemsChoice(plan)) return null;
  return (
    <MoveSection title={t("move_work_item.sections.sub_items")}>
      <p className="text-body-xs-regular text-tertiary">
        {t("move_work_item.sub_items_description", { count: plan.sub_items.length })}
      </p>
      <RadioGroup<TMoveSubItemsMode>
        density="compact"
        value={value}
        onValueChange={(next) => onChange(next)}
        disabled={disabled}
        aria-label={t("move_work_item.sections.sub_items")}
      >
        <label className="flex cursor-pointer items-center gap-2 text-body-xs-regular text-secondary">
          <Radio value="move" />
          {t("move_work_item.sub_items_move")}
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-body-xs-regular text-secondary">
          <Radio value="detach" />
          {t("move_work_item.sub_items_detach", { project: plan.source_project.name })}
        </label>
      </RadioGroup>
    </MoveSection>
  );
};

/** What is kept (mapped) in the target project. */
export const MoveKeptSummary = ({ plan }: { plan: TMovePlan }) => {
  const { t } = useTranslation();
  const lines: string[] = [];
  if (plan.labels.kept.length > 0)
    lines.push(`${t("move_work_item.kept.labels")}: ${plan.labels.kept.map((l) => l.target.name).join(", ")}`);
  if (plan.assignees.kept.length > 0)
    lines.push(`${t("move_work_item.kept.assignees")}: ${plan.assignees.kept.map((u) => u.display_name).join(", ")}`);
  if (plan.estimate.action === "kept" && plan.estimate.target)
    lines.push(`${t("move_work_item.kept.estimate")}: ${plan.estimate.target.value}`);
  if (plan.issue_type.action === "kept") lines.push(t("move_work_item.kept.issue_type"));
  if (plan.custom_properties.mapped.length > 0)
    lines.push(
      `${t("move_work_item.kept.custom_properties")}: ${plan.custom_properties.mapped.map((m) => m.target.display_name).join(", ")}`
    );
  if (lines.length === 0) return null;
  return (
    <MoveSection title={t("move_work_item.sections.kept")}>
      <ul className="list-disc space-y-1 pl-5 text-body-xs-regular text-secondary">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </MoveSection>
  );
};

/** Everything that will not survive the move. */
export const MoveDropsSummary = ({ plan }: { plan: TMovePlan }) => {
  const { t } = useTranslation();
  const rows = summarizeMoveDrops(plan);
  if (rows.length === 0) return null;
  return (
    <MoveSection title={t("move_work_item.sections.dropped")}>
      <Banner
        placement="inline"
        variant="warning"
        size="sm"
        description={
          <ul className="list-disc space-y-1 pl-4">
            {rows.map((row) => (
              <li key={row.kind}>
                <span className="font-medium">{t(`move_work_item.drops.${row.kind}`)}</span>
                {row.items.length > 0 && <span>{`: ${row.items.join(", ")}`}</span>}
              </li>
            ))}
          </ul>
        }
      />
    </MoveSection>
  );
};

export const MoveWorklogs = ({ plan }: { plan: TMovePlan }) => {
  const { t } = useTranslation();
  const { worklogs } = plan;
  if (worklogs.count === 0) return null;
  const warnings: string[] = [];
  if (!worklogs.target_time_tracking_enabled)
    warnings.push(t("move_work_item.worklogs.time_tracking_disabled", { project: plan.target_project.name }));
  if (worklogs.locked_count > 0) warnings.push(t("move_work_item.worklogs.locked", { count: worklogs.locked_count }));
  if (worklogs.running_timers > 0)
    warnings.push(t("move_work_item.worklogs.running_timers", { count: worklogs.running_timers }));
  if (worklogs.client_changes) warnings.push(t("move_work_item.worklogs.client_changes"));
  return (
    <MoveSection title={t("move_work_item.sections.worklogs")}>
      <p className="text-body-xs-regular text-secondary">
        {t("move_work_item.worklogs.summary", {
          count: worklogs.count,
          duration: formatMoveDuration(worklogs.total_minutes),
        })}
      </p>
      {hasWorklogWarnings(worklogs) && (
        <Banner
          placement="inline"
          variant="warning"
          size="sm"
          description={
            <ul className="list-disc space-y-1 pl-4">
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          }
        />
      )}
    </MoveSection>
  );
};

export const MoveCarried = ({ plan }: { plan: TMovePlan }) => {
  const { t } = useTranslation();
  const { carried } = plan;
  const parts = [
    carried.comments > 0 && t("move_work_item.carried.comments", { count: carried.comments }),
    carried.attachments > 0 && t("move_work_item.carried.attachments", { count: carried.attachments }),
    carried.links > 0 && t("move_work_item.carried.links", { count: carried.links }),
    carried.relations > 0 && t("move_work_item.carried.relations", { count: carried.relations }),
  ].filter(Boolean);
  return (
    <MoveSection title={t("move_work_item.sections.carried")}>
      <p className="text-body-xs-regular text-secondary">
        {parts.length > 0 ? `${parts.join(", ")}. ` : ""}
        {t("move_work_item.carried.history")}
      </p>
    </MoveSection>
  );
};
