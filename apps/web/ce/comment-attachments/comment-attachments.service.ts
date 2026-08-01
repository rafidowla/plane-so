/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: comment-attachments (#18)
import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";

/** Subset of FileAsset the comment-attachment UI consumes. */
export type TCommentAsset = {
  id: string;
  attributes: {
    name?: string;
    size?: number;
    type?: string;
  };
  asset_url: string;
};

/**
 * Client for the fork's comment-assets endpoint
 * (apps/api/plane/app/views/asset/v2.py ProjectCommentAssetsEndpoint).
 */
export class CommentAttachmentsService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async getCommentAssets(workspaceSlug: string, projectId: string, commentId: string): Promise<TCommentAsset[]> {
    return this.get(`/api/assets/v2/workspaces/${workspaceSlug}/projects/${projectId}/comments/${commentId}/assets/`)
      .then((res) => res?.data)
      .catch((err) => {
        throw err?.response?.data;
      });
  }
}

export const commentAttachmentsService = new CommentAttachmentsService();
