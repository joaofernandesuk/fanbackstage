import net from "node:net";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const apiPort = process.env.E2E_API_PORT ?? "38180";
const webPort = process.env.E2E_WEB_PORT ?? "38181";
const apiUrl = process.env.E2E_API_URL ?? `http://127.0.0.1:${apiPort}`;
const webUrl = process.env.E2E_WEB_URL ?? `http://127.0.0.1:${webPort}`;
const mailpitUrl = process.env.E2E_MAILPIT_URL ?? `http://127.0.0.1:${process.env.E2E_MAILPIT_UI_PORT ?? "8025"}`;
const mailpitSmtpPort = Number(process.env.E2E_MAILPIT_SMTP_PORT ?? process.env.FANBACKSTAGE_SMTP_PORT ?? "1025");
const redisUrl =
  process.env.FANBACKSTAGE_REDIS_URL ??
  `redis://127.0.0.1:${process.env.FANBACKSTAGE_REDIS_PORT ?? "6379"}/1`;
const notificationWebhookSecret =
  process.env.FANBACKSTAGE_NOTIFICATION_WEBHOOK_SECRET ??
  "fanbackstage-isolated-e2e-notification-webhook-secret";
const livekitPort = process.env.E2E_LIVEKIT_PORT ?? "17890";
const livekitTcpPort = process.env.E2E_LIVEKIT_TCP_PORT ?? "17891";
const livekitUdpPort = process.env.E2E_LIVEKIT_UDP_PORT ?? "17892";
const livekitContainer = `fanbackstage-e2e-livekit-${process.pid}`;
const livekitImage = process.env.E2E_LIVEKIT_IMAGE ?? "livekit/livekit-server:v1.13.5";
const databaseUrl = process.env.FANBACKSTAGE_DATABASE_URL;
const storageEndpoint =
  process.env.E2E_STORAGE_ENDPOINT_URL ?? process.env.FANBACKSTAGE_STORAGE_ENDPOINT_URL;

function isLoopbackHost(hostname) {
  return ["127.0.0.1", "localhost", "::1"].includes(hostname.replace(/^\[|\]$/g, "").toLowerCase());
}

function parseIsolatedUrl(rawValue, label, protocols) {
  if (!rawValue) {
    throw new Error(`${label} must be explicitly configured for an isolated E2E service.`);
  }
  let parsed;
  try {
    parsed = new URL(rawValue);
  } catch {
    throw new Error(`${label} must be a valid isolated-service URL.`);
  }
  if (!protocols.includes(parsed.protocol) || !isLoopbackHost(parsed.hostname)) {
    throw new Error(`${label} must use an approved protocol and a loopback host.`);
  }
  return parsed;
}

function assertIsolatedEnvironment() {
  if (process.env.FANBACKSTAGE_E2E_ISOLATED !== "1") {
    throw new Error(
      "Refusing destructive E2E setup without FANBACKSTAGE_E2E_ISOLATED=1 and explicit isolated service URLs.",
    );
  }
  if ((process.env.FANBACKSTAGE_ENVIRONMENT ?? "").trim().toLowerCase() === "production") {
    throw new Error("Refusing to run E2E setup from a production environment.");
  }
  const database = parseIsolatedUrl(databaseUrl, "FANBACKSTAGE_DATABASE_URL", [
    "postgresql+asyncpg:",
  ]);
  const databaseName = decodeURIComponent(database.pathname.replace(/^\/+/, ""));
  if (!/(^|[_-])(e2e|test)([_-]|$)/i.test(databaseName)) {
    throw new Error("The isolated E2E database name must contain an e2e or test boundary token.");
  }
  parseIsolatedUrl(redisUrl, "FANBACKSTAGE_REDIS_URL", ["redis:", "rediss:"]);
  parseIsolatedUrl(storageEndpoint, "E2E storage endpoint", ["http:", "https:"]);
  if (process.env.FANBACKSTAGE_STORAGE_PUBLIC_ENDPOINT_URL) {
    parseIsolatedUrl(
      process.env.FANBACKSTAGE_STORAGE_PUBLIC_ENDPOINT_URL,
      "FANBACKSTAGE_STORAGE_PUBLIC_ENDPOINT_URL",
      ["http:", "https:"],
    );
  }
  parseIsolatedUrl(mailpitUrl, "E2E_MAILPIT_URL", ["http:", "https:"]);
  parseIsolatedUrl(apiUrl, "E2E_API_URL", ["http:"]);
  parseIsolatedUrl(webUrl, "E2E_WEB_URL", ["http:"]);
  const smtpHost = (process.env.FANBACKSTAGE_SMTP_HOST ?? "localhost").trim();
  if (!isLoopbackHost(smtpHost)) {
    throw new Error("FANBACKSTAGE_SMTP_HOST must be loopback-only for E2E.");
  }
}

function assertPortFree(port, label) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", (error) => reject(new Error(`${label} port ${port} is occupied; refusing to attach Playwright to an existing service (${error.message}). Choose E2E_${label}_PORT.`)));
    server.listen({ host: "127.0.0.1", port: Number(port) }, () => server.close(resolve));
  });
}

async function waitForLiveKit(url, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(500) });
      if (response.status < 500) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Isolated LiveKit did not become ready at ${url} within ${timeoutMs}ms.`);
}

assertIsolatedEnvironment();
await assertPortFree(apiPort, "API");
await assertPortFree(webPort, "WEB");
await assertPortFree(livekitPort, "LIVEKIT");
await assertPortFree(livekitTcpPort, "LIVEKIT_TCP");
try {
  const response = await fetch(`${mailpitUrl}/api/v1/info`, { signal: AbortSignal.timeout(1500) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
} catch (error) {
  throw new Error(`Mailpit HTTP is unavailable at ${mailpitUrl}: ${error instanceof Error ? error.message : String(error)}`);
}
await new Promise((resolve, reject) => {
  const socket = net.createConnection({ host: "127.0.0.1", port: mailpitSmtpPort });
  socket.once("connect", () => { socket.end(); resolve(); });
  socket.once("error", (error) => reject(new Error(`Mailpit SMTP is unavailable at 127.0.0.1:${mailpitSmtpPort}: ${error.message}`)));
});
for (const [url, label] of [[apiUrl, "API"], [webUrl, "WEB"]]) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(500) });
    throw new Error(`${label} endpoint ${url} already responded with HTTP ${response.status}; refusing to attach Playwright to an existing service.`);
  } catch (error) {
    if (error instanceof Error && error.message.includes("refusing to attach")) throw error;
  }
}

const configTemplate = await readFile(resolve("../../infra/livekit/livekit.yaml"), "utf8");
if (!configTemplate.includes("host.docker.internal:38180")) {
  throw new Error("The E2E LiveKit config is missing its isolated API webhook placeholder.");
}
const configDirectory = await mkdtemp(join(tmpdir(), "fanbackstage-e2e-livekit-"));
const configPath = join(configDirectory, "livekit.yaml");
try {
  await writeFile(
    configPath,
    configTemplate.replace("host.docker.internal:38180", `host.docker.internal:${apiPort}`),
  );
} catch (error) {
  await rm(configDirectory, { recursive: true, force: true });
  throw error;
}
const livekit = spawn(
  "docker",
  [
    "run", "--rm", "--name", livekitContainer,
    "-p", `127.0.0.1:${livekitPort}:7880`,
    "-p", `127.0.0.1:${livekitTcpPort}:7881`,
    "-p", `127.0.0.1:${livekitUdpPort}:7882/udp`,
    "-v", `${configPath}:/etc/livekit.yaml:ro`,
    livekitImage, "--config", "/etc/livekit.yaml",
  ],
  { stdio: "inherit" },
);
const livekitExit = new Promise((resolve) => {
  livekit.once("exit", (code, signal) => resolve({ code, signal }));
  livekit.once("error", (error) => resolve({ error }));
});
const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function removeLiveKitContainer() {
  return new Promise((resolve) => {
    const remover = spawn("docker", ["rm", "-f", livekitContainer], { stdio: "ignore" });
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timeout);
      resolve();
    };
    const timeout = setTimeout(() => {
      remover.kill("SIGKILL");
      finish();
    }, 5_000);
    remover.once("exit", finish);
    remover.once("error", finish);
  });
}

let cleanupPromise;
function cleanup() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    try {
      if (livekit.exitCode === null && livekit.signalCode === null) {
        livekit.kill("SIGTERM");
        await Promise.race([livekitExit, delay(3_000)]);
      }
      // `docker run --rm` normally removes the container when the foreground
      // process exits. The exact-name fallback closes spawn races, daemon
      // failures, and already-exited-child cleanup without waiting forever.
      await removeLiveKitContainer();
      await Promise.race([livekitExit, delay(2_000)]);
    } finally {
      await rm(configDirectory, { recursive: true, force: true });
    }
  })();
  return cleanupPromise;
}
try {
  await Promise.race([
    waitForLiveKit(`http://127.0.0.1:${livekitPort}`),
    livekitExit.then(() => {
      throw new Error("The isolated LiveKit process exited before becoming ready.");
    }),
  ]);
} catch (error) {
  await cleanup();
  throw error;
}
const child = spawn("pnpm", ["exec", "playwright", "test", ...process.argv.slice(2)], {
  stdio: "inherit",
  env: {
    ...process.env,
    E2E_API_PORT: apiPort,
    E2E_WEB_PORT: webPort,
    E2E_API_URL: apiUrl,
    E2E_WEB_URL: webUrl,
    FANBACKSTAGE_E2E_RUNNER_VALIDATED: "1",
    // Playwright passes this broker to its API/worker webServer. Keep it in the
    // test process too so subprocess release harnesses enqueue onto that worker.
    FANBACKSTAGE_REDIS_URL: redisUrl,
    FANBACKSTAGE_NOTIFICATION_WEBHOOK_SECRET: notificationWebhookSecret,
    E2E_LIVEKIT_PORT: livekitPort,
    FANBACKSTAGE_LIVEKIT_URL: `ws://127.0.0.1:${livekitPort}`,
  },
});
let exitRequested = false;
function finish(exitCode) {
  if (exitRequested) return;
  exitRequested = true;
  void cleanup().finally(() => process.exit(exitCode));
}
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => finish(1));
}
child.once("error", () => finish(1));
child.once("exit", (code) => finish(code ?? 1));
