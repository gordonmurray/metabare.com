import { defineConfig } from "@playwright/test";

// APP_URL tests a deployed site instead of the local dev server.
const url = process.env.APP_URL ?? "http://localhost:5174/";
const browsers = (process.env.APP_BROWSERS ?? "chromium").split(",");

export default defineConfig({
    testDir: "tests/e2e",
    timeout: 10 * 60 * 1000,
    workers: 1,
    reporter: "list",
    use: { baseURL: url },
    projects: browsers.map((name) => ({ name, use: { browserName: name as "chromium" } })),
    webServer: process.env.APP_URL
        ? undefined
        : {
              command: "npx vite --port 5174 --strictPort",
              url,
              reuseExistingServer: true,
          },
});
