import net from "node:net";
import { spawn } from "node:child_process";

const apiPort = process.env.E2E_API_PORT ?? "38180";
const webPort = process.env.E2E_WEB_PORT ?? "38181";
const apiUrl = process.env.E2E_API_URL ?? `http://127.0.0.1:${apiPort}`;
const webUrl = process.env.E2E_WEB_URL ?? `http://127.0.0.1:${webPort}`;

function assertPortFree(port, label) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", (error) => reject(new Error(`${label} port ${port} is occupied; refusing to attach Playwright to an existing service (${error.message}). Choose E2E_${label}_PORT.`)));
    server.listen({ host: "127.0.0.1", port: Number(port) }, () => server.close(resolve));
  });
}

await assertPortFree(apiPort, "API");
await assertPortFree(webPort, "WEB");
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
