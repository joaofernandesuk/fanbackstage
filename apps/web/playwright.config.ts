import { defineConfig } from "@playwright/test";

const apiPort = process.env.E2E_API_PORT ?? "38180";
const webPort = process.env.E2E_WEB_PORT ?? "38181";
const apiUrl = process.env.E2E_API_URL ?? `http://127.0.0.1:${apiPort}`;
const webUrl = process.env.E2E_WEB_URL ?? `http://127.0.0.1:${webPort}`;
const mailpitUiPort = process.env.E2E_MAILPIT_UI_PORT ?? "8025";
const mailpitSmtpPort = process.env.E2E_MAILPIT_SMTP_PORT ?? process.env.FANBACKSTAGE_SMTP_PORT ?? "1025";
// The API must sign browser-reachable object-storage URLs. Keep this E2E
// override separate from production storage configuration so isolated stacks
// can use a non-default Compose port without changing application behavior.
const storageEndpoint =
  process.env.E2E_STORAGE_ENDPOINT_URL ??
  process.env.FANBACKSTAGE_STORAGE_ENDPOINT_URL ??
  `http://127.0.0.1:${process.env.E2E_MINIO_PORT ?? process.env.FANBACKSTAGE_MINIO_PORT ?? "9000"}`;
const environment = {
  ...process.env,
  FANBACKSTAGE_DATABASE_URL:
    process.env.FANBACKSTAGE_DATABASE_URL ??
    "postgresql+asyncpg://fanbackstage:fanbackstage@127.0.0.1:5432/fanbackstage",
  FANBACKSTAGE_REDIS_URL: process.env.FANBACKSTAGE_REDIS_URL ?? "redis://127.0.0.1:6379/1",
  // Browser scenarios exercise many legitimate account transitions in one
  // isolated worker. Keep production throttling intact while avoiding a
  // shared test-worker counter masking the financial assertions.
  FANBACKSTAGE_AUTH_RATE_LIMIT_ATTEMPTS: "1000",
  FANBACKSTAGE_SMTP_PORT: mailpitSmtpPort,
  E2E_MAILPIT_URL: process.env.E2E_MAILPIT_URL ?? `http://127.0.0.1:${mailpitUiPort}`,
  FANBACKSTAGE_STORAGE_ENDPOINT_URL: storageEndpoint,
  FANBACKSTAGE_WEB_ORIGIN: webUrl,
  NEXT_PUBLIC_FANBACKSTAGE_API_URL: apiUrl,
  E2E_API_URL: apiUrl,
  E2E_WEB_URL: webUrl,
};
const apiCommand = (command: string) =>
  process.env.E2E_API_RUNNER ? `${process.env.E2E_API_RUNNER}${command}` : `uv run ${command}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 120000,
  workers: 1,
  use: {
    baseURL: webUrl,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    permissions: ["camera", "microphone"],
    launchOptions: { args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] },
  },
  webServer: [
    {
      command: "./scripts/start-e2e-api.sh",
      url: `${apiUrl}/ready`,
      reuseExistingServer: false,
      env: environment,
    },
    { command: `pnpm dev --hostname 127.0.0.1 --port ${webPort}`, url: webUrl, reuseExistingServer: false, env: environment },
  ],
});
