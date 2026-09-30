import path from "node:path";
import * as dotenv from "dotenv";
import { reactRouter } from "@react-router/dev/vite";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

dotenv.config({ path: path.resolve(__dirname, ".env") });

// Expose only vars starting with VITE_
const viteEnv = Object.keys(process.env)
  .filter((k) => k.startsWith("VITE_"))
  .reduce<Record<string, string>>((a, k) => {
    a[k] = process.env[k] ?? "";
    return a;
  }, {});

export default defineConfig(() => ({
  define: {
    "process.env": JSON.stringify(viteEnv),
  },
  build: {
    assetsInlineLimit: 0,
  },
  plugins: [reactRouter(), tsconfigPaths({ projects: [path.resolve(__dirname, "tsconfig.json")] })],
  resolve: {
    alias: {
      // Next.js compatibility shims used within web
      "next/link": path.resolve(__dirname, "app/compat/next/link.tsx"),
      "next/navigation": path.resolve(__dirname, "app/compat/next/navigation.ts"),
      "next/script": path.resolve(__dirname, "app/compat/next/script.tsx"),
    },
    dedupe: ["react", "react-dom"],
  },
  server: {
    host: "127.0.0.1",
    // FORK: dev-only /api proxy. The `define` below replaces `process.env`
    // wholesale, which vite does NOT apply to workspace-package source served
    // in dev — so API_BASE_URL resolves to "" and every API call hits the SPA
    // shell as text/html. Proxying /api makes relative calls work regardless,
    // and keeps the session cookie same-origin.
    // Deliberately NOT reading VITE_API_BASE_URL here: dotenv loads
    // apps/web/.env into process.env first, and that file historically points
    // at :8001 — a port the local docker stack does not publish. The local
    // stack's API is on 127.0.0.1:8000 (docker-compose-local.yml); override
    // with DEV_API_PROXY_TARGET when needed.
    proxy: {
      "/api": {
        target: process.env.DEV_API_PROXY_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      // auth service calls the /auth/* endpoints without the /api prefix
      "/auth": {
        target: process.env.DEV_API_PROXY_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  // No SSR-specific overrides needed; alias resolves to ESM build
}));
