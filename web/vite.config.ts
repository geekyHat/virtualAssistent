/// <reference types="vitest/config" />
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  // Solo configurazione pubblica; il .env con DSN nella radice resta al launcher.
  const env = loadEnv(mode, ".", ["NEWRAY_PORT", "NEWRAY_WEB_MARKER"]);
  return {
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    plugins: [
      react(),
      tailwindcss(),
      {
        name: "newray-launcher-marker",
        apply: "serve",
        transformIndexHtml(html) {
          const marker = env.NEWRAY_WEB_MARKER;
          return marker && /^[a-f0-9]{16}$/.test(marker)
            ? html.replace("</head>", `<meta name="newray-workspace" content="${marker}" /></head>`)
            : html;
        },
      },
    ],
    server: {
      // Dev: proxy same-origin verso l'API (NewRay.md §18.4): i cookie di
      // sessione funzionano senza CORS `*` sulle API autenticate.
      host: "127.0.0.1",
      proxy: {
        "/api": { target: `http://127.0.0.1:${env.NEWRAY_PORT || "8000"}` },
        "/openapi.json": { target: `http://127.0.0.1:${env.NEWRAY_PORT || "8000"}` },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      // Vitest sta in `src/`; i test browser Playwright vivono in `e2e/`
      // e non condividono runtime né matcher.
      include: ["src/**/*.{test,spec}.{ts,tsx}"],
      exclude: ["e2e/**", "node_modules/**", "dist/**"],
    },
  };
});
