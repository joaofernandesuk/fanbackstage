import { defineConfig } from "@playwright/test";

const apiPort = process.env.E2E_API_PORT ?? "8000";
const apiUrl = `http://127.0.0.1:${apiPort}`;
const environment = {
  ...process.env,
  FANBACKSTAGE_DATABASE_URL:
    process.env.FANBACKSTAGE_DATABASE_URL ??
    "postgresql+asyncpg://fanbackstage:fanbackstage@127.0.0.1:5432/fanbackstage",
  FANBACKSTAGE_REDIS_URL: process.env.FANBACKSTAGE_REDIS_URL ?? "redis://127.0.0.1:6379/1",
  FANBACKSTAGE_SMTP_PORT: process.env.FANBACKSTAGE_SMTP_PORT ?? "1025",
  FANBACKSTAGE_STORAGE_ENDPOINT_URL:
    process.env.FANBACKSTAGE_STORAGE_ENDPOINT_URL ?? "http://127.0.0.1:9000",
  FANBACKSTAGE_WEB_ORIGIN: "http://127.0.0.1:31000",
  NEXT_PUBLIC_FANBACKSTAGE_API_URL: apiUrl,
};
const apiCommand = (command: string) =>
  process.env.E2E_API_RUNNER ? `${process.env.E2E_API_RUNNER}${command}` : `uv run ${command}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 120000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:31000", trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: [
    {
      command: `cd ../api && ${apiCommand("alembic upgrade head")} && ${apiCommand("python tests/e2e_seed.py")} && (${apiCommand("celery -A app.worker.celery_app worker --loglevel=WARNING")} &) && ${apiCommand(`uvicorn app.main:app --host 127.0.0.1 --port ${apiPort}`)}`,
      url: `${apiUrl}/ready`,
      reuseExistingServer: false,
      env: environment,
    },
    { command: "pnpm dev --hostname 127.0.0.1 --port 31000", url: "http://127.0.0.1:31000", reuseExistingServer: false, env: environment },
  ],
});
