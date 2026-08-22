import net from "node:net";
import { spawn } from "node:child_process";

const apiPort = process.env.E2E_API_PORT ?? "38180";
const webPort = process.env.E2E_WEB_PORT ?? "38181";
const apiUrl = process.env.E2E_API_URL ?? `http://127.0.0.1:${apiPort}`;
const webUrl = process.env.E2E_WEB_URL ?? `http://127.0.0.1:${webPort}`;
const mailpitUrl = process.env.E2E_MAILPIT_URL ?? `http://127.0.0.1:${process.env.E2E_MAILPIT_UI_PORT ?? "8025"}`;
const mailpitSmtpPort = Number(process.env.E2E_MAILPIT_SMTP_PORT ?? process.env.FANBACKSTAGE_SMTP_PORT ?? "1025");

function assertPortFree(port, label) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", (error) => reject(new Error(`${label} port ${port} is occupied; refusing to attach Playwright to an existing service (${error.message}). Choose E2E_${label}_PORT.`)));
    server.listen({ host: "127.0.0.1", port: Number(port) }, () => server.close(resolve));
  });
}

await assertPortFree(apiPort, "API");
await assertPortFree(webPort, "WEB");
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
const child = spawn("pnpm", ["exec", "playwright", "test", ...process.argv.slice(2)], {
  stdio: "inherit",
  env: { ...process.env, E2E_API_PORT: apiPort, E2E_WEB_PORT: webPort, E2E_API_URL: apiUrl, E2E_WEB_URL: webUrl },
});
child.on("exit", (code) => process.exit(code ?? 1));
