/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// types
import { API_BASE_URL } from "@plane/constants";
import type {
  IProjectBulkAddFormData,
  TProjectInviteByEmailPayload,
  TProjectInviteByEmailResponse,
  TProjectMembership,
  TProjectPendingMember,
} from "@plane/types";
// services
import { APIService } from "@/services/api.service";

export class ProjectMemberService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async fetchProjectMembers(workspaceSlug: string, projectId: string): Promise<TProjectMembership[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/members/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async bulkAddMembersToProject(
    workspaceSlug: string,
    projectId: string,
    data: IProjectBulkAddFormData
  ): Promise<TProjectMembership[]> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/members/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  // FORK: PSR-124 -- invite people to a project by email
  async inviteByEmail(
    workspaceSlug: string,
    projectId: string,
    data: TProjectInviteByEmailPayload
  ): Promise<TProjectInviteByEmailResponse> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/members/invite-by-email/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async fetchPendingMembers(workspaceSlug: string, projectId: string): Promise<TProjectPendingMember[]> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pending-members/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async cancelPendingMember(workspaceSlug: string, projectId: string, spotId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pending-members/${spotId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async resendPendingMember(workspaceSlug: string, projectId: string, spotId: string): Promise<void> {
    return this.post(`/api/workspaces/${workspaceSlug}/projects/${projectId}/pending-members/${spotId}/resend/`, {})
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async projectMemberMe(workspaceSlug: string, projectId: string): Promise<TProjectMembership> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/project-members/me/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async getProjectMember(workspaceSlug: string, projectId: string, memberId: string): Promise<TProjectMembership> {
    return this.get(`/api/workspaces/${workspaceSlug}/projects/${projectId}/members/${memberId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async updateProjectMember(
    workspaceSlug: string,
    projectId: string,
    memberId: string,
    data: Partial<TProjectMembership>
  ): Promise<TProjectMembership> {
    return this.patch(`/api/workspaces/${workspaceSlug}/projects/${projectId}/members/${memberId}/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async deleteProjectMember(workspaceSlug: string, projectId: string, memberId: string): Promise<void> {
    return this.delete(`/api/workspaces/${workspaceSlug}/projects/${projectId}/members/${memberId}/`)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }
}

const projectMemberService = new ProjectMemberService();

export default projectMemberService;
