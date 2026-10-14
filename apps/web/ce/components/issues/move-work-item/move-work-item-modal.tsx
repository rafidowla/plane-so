/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: move-work-item (PSR-91) — "Move to project" dialog (docs/move-work-item-design.md §6)
import { useCallback, useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
// plane imports
import { Banner } from "@makeplane/propel/components/banner";
import { Button } from "@makeplane/propel/components/button";
import {
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogHeading,
  DialogMain,
  DialogTitle,
} from "@makeplane/propel/components/dialog";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Spinner } from "@plane/blocks/spinner";
import { setToast } from "@plane/blocks/toast";
import type { TIssue } from "@plane/types";
import type { TMoveNavigationContext, TMovePlan, TMoveResult, TMoveSubItemsMode } from "@plane/utils";
import {
  buildMoveStateMap,
  DEFAULT_MOVE_SUB_ITEMS_MODE,
  formatMoveConfirmLabel,
  getMovedWorkItemHref,
  getPostMoveNavigation,
  getUnmappedSourceStateIds,
} from "@plane/utils";
// components
import { ProjectSelect } from "@/components/dropdowns/project/project-select";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import type { TWorkItemMoveError } from "@/services/issue/work-item-move.service";
import { workItemMoveService } from "@/services/issue/work-item-move.service";
// local imports
import {
  MoveArchivedSubItems,
  MoveCarried,
  MoveDropsSummary,
  MoveKeptSummary,
  MoveStateMapping,
  MoveSubItemsChoice,
  MoveWorklogs,
} from "./move-plan-sections";
import { useApplyWorkItemMove } from "./use-apply-work-item-move";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  issue: TIssue;
  /** Where the dialog was opened from; decides what happens after the move (navigate / close peek / stay). */
  context?: TMoveNavigationContext;
  onMoved?: (result: TMoveResult) => void;
};

const getErrorMessage = (error: unknown, fallback: string) =>
  (error as TWorkItemMoveError | undefined)?.error || fallback;

export const MoveWorkItemModal = observer(function MoveWorkItemModal(props: Props) {
  const { isOpen, onClose, issue, context = "list", onMoved } = props;
  // router
  const { workspaceSlug } = useParams();
  const router = useAppRouter();
  const { t } = useTranslation();
  // states
  const [targetProjectId, setTargetProjectId] = useState<string | null>(null);
  const [subItems, setSubItems] = useState<TMoveSubItemsMode>(DEFAULT_MOVE_SUB_ITEMS_MODE);
  const [plan, setPlan] = useState<TMovePlan | null>(null);
  const [isLoadingPlan, setIsLoadingPlan] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [stateOverrides, setStateOverrides] = useState<Record<string, string | undefined>>({});
  const [isMoving, setIsMoving] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  // refs: ignore responses of superseded preview requests
  const requestIdRef = useRef(0);
  // store hooks
  const { joinedProjectIds, getProjectById, getProjectIdentifierById } = useProject();
  const { allowPermissions } = useUserPermissions();
  const { setPeekIssue } = useIssueDetail();
  const applyWorkItemMove = useApplyWorkItemMove();
  // derived values
  const slug = workspaceSlug?.toString() ?? "";
  const sourceProjectId = issue.project_id ?? "";
  const currentIdentifier = `${getProjectIdentifierById(issue.project_id) ?? ""}-${issue.sequence_id}`;
  const canMoveInto = useCallback(
    (projectId: string) =>
      projectId !== sourceProjectId &&
      !getProjectById(projectId)?.archived_at &&
      allowPermissions(
        [EUserPermissions.ADMIN, EUserPermissions.MEMBER],
        EUserPermissionsLevel.PROJECT,
        slug,
        projectId
      ),
    [allowPermissions, getProjectById, slug, sourceProjectId]
  );
  const hasTargetProjects = (joinedProjectIds ?? []).some((id) => canMoveInto(id));
  const unmappedStateIds = plan ? getUnmappedSourceStateIds(plan, stateOverrides) : [];
  const canConfirm = !!plan && !isLoadingPlan && !planError && !isMoving && unmappedStateIds.length === 0;

  // Reset everything whenever the dialog is (re)opened or closed.
  useEffect(() => {
    requestIdRef.current += 1;
    setTargetProjectId(null);
    setSubItems(DEFAULT_MOVE_SUB_ITEMS_MODE);
    setPlan(null);
    setPlanError(null);
    setIsLoadingPlan(false);
    setStateOverrides({});
    setMoveError(null);
    setIsMoving(false);
  }, [isOpen]);

  // Changing the target project invalidates the plan and the per-state overrides made against the old one.
  const handleTargetProjectChange = (projectId: string | null) => {
    if (!projectId || projectId === targetProjectId) return;
    setTargetProjectId(projectId);
    setPlan(null);
    setStateOverrides({});
    setMoveError(null);
  };

  // Fetch the preview whenever the target project or the sub-items choice changes.
  useEffect(() => {
    if (!isOpen || !targetProjectId || !slug || !sourceProjectId) return;
    const requestId = ++requestIdRef.current;
    setIsLoadingPlan(true);
    setPlanError(null);
    workItemMoveService
      .getMovePreview(slug, sourceProjectId, issue.id, targetProjectId, subItems)
      .then((response) => {
        if (requestId !== requestIdRef.current) return;
        setPlan(response);
        return;
      })
      .catch((error: unknown) => {
        if (requestId !== requestIdRef.current) return;
        setPlan(null);
        setPlanError(getErrorMessage(error, t("move_work_item.preview_failed")));
      })
      .finally(() => {
        if (requestId === requestIdRef.current) setIsLoadingPlan(false);
      });
    // `t` is stable per language and must not re-trigger a fetch.
    // oxlint-disable-next-line eslint-plugin-react-hooks/exhaustive-deps
  }, [isOpen, targetProjectId, subItems, slug, sourceProjectId, issue.id]);

  const handleClose = () => {
    if (isMoving) return;
    onClose();
  };

  const handleMove = async () => {
    if (!plan || !targetProjectId || !canConfirm) return;
    setIsMoving(true);
    setMoveError(null);
    try {
      const result = await workItemMoveService.moveWorkItem(slug, sourceProjectId, issue.id, {
        target_project_id: targetProjectId,
        sub_items: subItems,
        state_map: buildMoveStateMap(plan, stateOverrides),
      });
      const href = getMovedWorkItemHref(slug, result.identifier);
      const navigation = getPostMoveNavigation(context, slug, result);
      // The row (and with it this dialog) can unmount once the stores drop the item, so announce first.
      setToast({
        type: "success",
        title: t("move_work_item.toast.success_title"),
        message: t("move_work_item.toast.success_message", { identifier: result.identifier }),
        actionItems:
          navigation.type === "navigate"
            ? undefined
            : [{ label: t("common.view"), href, target: "_blank", primary: true }],
      });
      applyWorkItemMove(result);
      onClose();
      if (navigation.type === "navigate") router.push(navigation.href);
      else if (navigation.type === "close-peek") setPeekIssue(undefined);
      onMoved?.(result);
    } catch (error) {
      setMoveError(getErrorMessage(error, t("move_work_item.toast.error_message")));
    } finally {
      setIsMoving(false);
    }
  };

  const targetProject = plan?.target_project ?? (targetProjectId ? getProjectById(targetProjectId) : undefined);

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) handleClose();
      }}
    >
      {/*
        data-prevent-outside-click keeps an open peek overview from collapsing while this portaled dialog is used.
        The dialog is mounted inside list rows / peek, and React events bubble through portals: swallow clicks here so
        they never reach a clickable row behind it.
      */}
      <DialogContent size="md" data-prevent-outside-click onClick={(e) => e.stopPropagation()}>
        <DialogMain>
          <DialogHeader>
            <DialogHeading>
              <DialogTitle>{t("move_work_item.title", { identifier: currentIdentifier })}</DialogTitle>
            </DialogHeading>
          </DialogHeader>
          <DialogBody tabIndex={0}>
            <div className="space-y-5">
              <p className="text-body-xs-regular text-tertiary">{t("move_work_item.description")}</p>

              <section className="space-y-2">
                <h4 className="text-body-xs-semibold text-primary">{t("move_work_item.target_project")}</h4>
                {hasTargetProjects ? (
                  <div>
                    <ProjectSelect
                      value={targetProjectId}
                      onChange={handleTargetProjectChange}
                      multiple={false}
                      variant="select-lg"
                      placeholder={t("move_work_item.select_project")}
                      excludeProjectId={sourceProjectId}
                      filterOption={canMoveInto}
                      disabled={isMoving}
                    />
                  </div>
                ) : (
                  <p className="text-body-xs-regular text-tertiary">{t("move_work_item.no_projects")}</p>
                )}
                {plan && (
                  <p className="text-body-xs-regular text-secondary">
                    {t("move_work_item.new_identifier", {
                      current: plan.work_item.identifier,
                      next: plan.next_identifier,
                    })}
                  </p>
                )}
              </section>

              {planError && <Banner placement="inline" variant="danger" size="sm" description={planError} />}

              {isLoadingPlan && !plan && (
                <div className="flex items-center gap-2 text-body-xs-regular text-tertiary">
                  <Spinner className="h-4 w-4" />
                  {t("move_work_item.loading_preview")}
                </div>
              )}

              {plan && (
                <div className={isLoadingPlan ? "pointer-events-none space-y-5 opacity-60" : "space-y-5"}>
                  <MoveSubItemsChoice plan={plan} value={subItems} onChange={setSubItems} disabled={isMoving} />
                  <MoveArchivedSubItems plan={plan} />
                  <MoveStateMapping
                    plan={plan}
                    overrides={stateOverrides}
                    onOverride={(sourceStateId, targetStateId) =>
                      setStateOverrides((prev) => ({ ...prev, [sourceStateId]: targetStateId }))
                    }
                    disabled={isMoving}
                  />
                  <MoveKeptSummary plan={plan} />
                  <MoveDropsSummary plan={plan} />
                  <MoveWorklogs plan={plan} />
                  <MoveCarried plan={plan} />
                </div>
              )}

              {moveError && <Banner placement="inline" variant="danger" size="sm" description={moveError} />}
            </div>
          </DialogBody>
        </DialogMain>
        <DialogActions>
          <Button
            variant="secondary"
            size="md"
            stretch="auto"
            type="button"
            label={t("common.cancel")}
            onClick={handleClose}
            disabled={isMoving}
          />
          <Button
            variant="primary"
            size="md"
            stretch="auto"
            type="button"
            loading={isMoving}
            disabled={!canConfirm}
            label={
              isMoving
                ? t("move_work_item.moving")
                : formatMoveConfirmLabel(targetProject, t("move_work_item.confirm_prefix"))
            }
            onClick={handleMove}
          />
        </DialogActions>
      </DialogContent>
    </Dialog>
  );
});
