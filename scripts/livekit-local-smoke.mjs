#!/usr/bin/env node
import { createServer } from "node:http";
import { createHmac } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("../apps/web/node_modules/@playwright/test");

const base64Url = (value) => Buffer.from(value).toString("base64url");
const signedToken = () => {
  const header = base64Url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = base64Url(JSON.stringify({
    iss: "devkey",
    sub: "local-camera-smoke",
    exp: Math.floor(Date.now() / 1000) + 60,
    video: {
      room: "fanbackstage-local-smoke",
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
      canPublishData: false,
    },
  }));
  const body = `${header}.${payload}`;
  return `${body}.${createHmac("sha256", "fanbackstage-livekit-development-secret-2026").update(body).digest("base64url")}`;
};

const clientBundle = await readFile(
  new URL("../apps/web/node_modules/livekit-client/dist/livekit-client.umd.js", import.meta.url),
);
const token = signedToken();
const html = `<!doctype html><meta charset="utf-8"><script src="/livekit-client.js"></script><script>
  window.addEventListener("load", async () => {
    const output = document.body.appendChild(document.createElement("pre"));
    try {
      const room = new LivekitClient.Room();
      await room.connect("ws://127.0.0.1:17880", ${JSON.stringify(token)});
      await room.localParticipant.enableCameraAndMicrophone();
      const hasCamera = [...room.localParticipant.trackPublications.values()].some(
        (publication) => publication.source === LivekitClient.Track.Source.Camera,
      );
      output.textContent = JSON.stringify({ connected: room.state === LivekitClient.ConnectionState.Connected, hasCamera });
      await room.disconnect();
    } catch (error) {
      output.textContent = JSON.stringify({ error: String(error && error.stack || error) });
    }
  });
</script>`;

const server = createServer((request, response) => {
  if (request.url === "/livekit-client.js") {
    response.writeHead(200, { "content-type": "text/javascript" });
    response.end(clientBundle);
    return;
  }
  response.writeHead(200, { "content-type": "text/html" });
  response.end(html);
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const { port } = server.address();
let browser;
try {
  browser = await chromium.launch({
    headless: true,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
  });
  const context = await browser.newContext({ permissions: ["camera", "microphone"] });
  const page = await context.newPage();
  await page.goto(`http://127.0.0.1:${port}`);
  await page.waitForFunction(() => document.body.textContent.trim().startsWith("{"), undefined, { timeout: 15_000 });
  const result = JSON.parse((await page.textContent("body"))?.trim() ?? "{}");
  if (!result.connected || !result.hasCamera) {
    throw new Error(`Local LiveKit camera smoke failed: ${JSON.stringify(result)}`);
  }
  console.log("Local LiveKit camera smoke passed: connected and published a camera track.");
} finally {
  await browser?.close();
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}
