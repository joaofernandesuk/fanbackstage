import { defineConfig } from "@playwright/test";
export default defineConfig({ testDir: "./e2e", timeout: 60000, use: { baseURL: "http://127.0.0.1:31000", trace: "retain-on-failure", screenshot: "only-on-failure" }, webServer: { command: "pnpm dev --hostname 127.0.0.1 --port 31000", url: "http://127.0.0.1:31000", reuseExistingServer: false } });
