import { defineConfig } from "@playwright/test";

// The browser is launched by the test itself, with a persistent profile;
// see tests/spike.spec.ts. SPIKE_CHANNEL=chrome uses the installed Google
// Chrome, which can reach the real GPU.
export default defineConfig({
  testDir: "tests",
  timeout: 30 * 60 * 1000,
  workers: 1,
  reporter: "list",
  webServer: {
    command: "npx vite --port 5173 --strictPort",
    url: "http://localhost:5173",
    reuseExistingServer: true,
  },
});
