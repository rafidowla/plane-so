/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// FORK: PSR-86 -- POST <LIVE_BASE_PATH>/page-content/replace, called by the API server only.

import type { Hocuspocus } from "@hocuspocus/server";
import type { Request, Response } from "express";
import { z } from "zod";
// plane imports
import { Controller, Middleware, Post } from "@plane/decorators";
import { logger } from "@plane/logger";
// lib
import { requireSecretKey } from "@/lib/auth-middleware";
import { PageContentReplaceError, replacePageContent, replacePageContentSchema } from "@/lib/page-content-replace";

@Controller("/page-content")
export class PageContentController {
  [key: string]: unknown;
  private readonly hocusPocusServer: Hocuspocus;

  constructor(hocusPocusServer: Hocuspocus) {
    this.hocusPocusServer = hocusPocusServer;
  }

  @Post("/replace")
  @Middleware(requireSecretKey)
  async replace(req: Request, res: Response) {
    try {
      const body = replacePageContentSchema.parse(req.body);
      const result = await replacePageContent(this.hocusPocusServer, body);
      return res.status(200).json(result);
    } catch (error) {
      if (error instanceof z.ZodError) {
        const validationErrors = error.errors.map((err) => ({
          path: err.path.join("."),
          message: err.message,
        }));
        return res.status(400).json({ message: "Validation error", context: { validationErrors } });
      }
      if (error instanceof PageContentReplaceError) {
        return res.status(400).json({ message: error.message });
      }
      logger.error("PAGE_CONTENT_CONTROLLER: Internal server error", error);
      return res.status(500).json({ message: "Internal server error." });
    }
  }
}
