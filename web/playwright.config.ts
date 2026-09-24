import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright NewRay (B-09.1).
 *
 * Il progetto ha due profili distinti (§22.4):
 *  - `ui`: test browser su trasporto controllato (server dev Vite + route
 *    mock nelle fixture). Nessun backend, nessun database, nessuna GPU.
 *  - `live`: workflow browser → API → PostgreSQL reali, avviati dal
 *    launcher personale prima di `npx playwright test --project=live`.
 *    Opt-in via `NEWRAY_E2E_LIVE=1`; non parte in CI di default.
 *
 * Browser bloccati dal lockfile del package (`@playwright/test`);
 * l'installazione dei binari è esplicita (`npx playwright install`).
 */

const LIVE = process.env.NEWRAY_E2E_LIVE === "1";
const UI_PORT = Number(process.env.NEWRAY_E2E_UI_PORT ?? 4318);
const LIVE_BASE = process.env.NEWRAY_E2E_LIVE_BASE ?? "http://127.0.0.1:5173";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    locale: "it-IT",
    timezoneId: "Europe/Rome",
  },
  projects: [
    {
      name: "ui",
      testMatch: /e2e\/ui\/.*\.spec\.ts$/,
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${UI_PORT}` },
    },
    ...(LIVE
      ? [
          {
            name: "live",
            testMatch: /e2e\/live\/.*\.spec\.ts$/,
            use: { ...devices["Desktop Chrome"], baseURL: LIVE_BASE },
          },
        ]
      : []),
  ],
  webServer: [
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${UI_PORT} --strictPort`,
      url: `http://127.0.0.1:${UI_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: {
        // Nessun backend reale: le rotte API sono intercettate dalle fixture.
        NEWRAY_E2E_UI: "1",
      },
    },
  ],
});
