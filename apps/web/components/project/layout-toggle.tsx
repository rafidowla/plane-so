/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-123 -- List / Grid toggle for the Projects page header.

import { observer } from "mobx-react";
import { GridOutline, ListOutline } from "@makeplane/propel/icons";
import { Icon } from "@makeplane/propel/components/icon";
import { IconButton } from "@makeplane/propel/components/icon-button";
import { useTranslation } from "@plane/i18n";
import { useProjectsLayout } from "./use-projects-layout";

export const ProjectsLayoutToggle = observer(function ProjectsLayoutToggle() {
  const { t } = useTranslation();
  const { layout, isLoaded, setLayout } = useProjectsLayout();

  return (
    <div role="group" aria-label={t("workspace_projects.list_view.layout_label")} className="flex items-center gap-0.5">
      <IconButton
        variant={layout === "list" ? "secondary" : "ghost"}
        size="md"
        icon={<Icon icon={ListOutline} />}
        aria-label={t("workspace_projects.list_view.layout_list")}
        aria-pressed={layout === "list"}
        disabled={!isLoaded}
        onClick={() => setLayout("list")}
      />
      <IconButton
        variant={layout === "grid" ? "secondary" : "ghost"}
        size="md"
        icon={<Icon icon={GridOutline} />}
        aria-label={t("workspace_projects.list_view.layout_grid")}
        aria-pressed={layout === "grid"}
        disabled={!isLoaded}
        onClick={() => setLayout("grid")}
      />
    </div>
  );
});
