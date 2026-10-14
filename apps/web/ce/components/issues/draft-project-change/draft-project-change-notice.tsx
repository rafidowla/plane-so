/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: draft-project-change (PSR-92) — inline notice listing what the project switch dropped from the draft
import { observer } from "mobx-react";
import { Banner } from "@makeplane/propel/components/banner";
import { useTranslation } from "@plane/i18n";
// hooks
import { useProject } from "@/hooks/store/use-project";
// local imports
import type { TDraftProjectChangeNotice } from "./use-draft-project-change";

type Props = {
  notice: TDraftProjectChangeNotice | null;
  onDismiss: () => void;
};

export const DraftProjectChangeNotice = observer(function DraftProjectChangeNotice(props: Props) {
  const { notice, onDismiss } = props;
  const { t } = useTranslation();
  const { getProjectById } = useProject();

  if (!notice || notice.rows.length === 0) return null;
  const projectName = getProjectById(notice.projectId)?.name ?? "";

  return (
    <Banner
      placement="inline"
      variant="warning"
      size="sm"
      title={t("draft_project_change.notice_title", { project: projectName })}
      dismissLabel={t("draft_project_change.dismiss")}
      onDismiss={onDismiss}
      description={
        <>
          <span>{t("draft_project_change.notice_description")}</span>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {notice.rows.map((row) => (
              <li key={row.kind}>
                <span className="font-medium">{t(`draft_project_change.drops.${row.kind}`)}</span>
                {row.items.length > 0 && <span>{`: ${row.items.join(", ")}`}</span>}
              </li>
            ))}
          </ul>
        </>
      }
    />
  );
});
