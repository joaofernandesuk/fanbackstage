import { expect, test } from "@playwright/test";

import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };

async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown) {
  return page.evaluate(async ({ apiBase, path, method, body }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, { method, credentials: "include", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { apiBase, path, method, body });
}

async function login(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login"); await page.getByLabel("Email").fill(email); await page.getByLabel("Password").fill(password); await page.getByRole("button", { name: "Log in" }).click(); await expect(page.getByText(email)).toBeVisible();
}

async function register(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/register"); await page.getByLabel("Email").fill(email); await page.getByLabel("Password").fill(password); await page.getByRole("button", { name: "Create account" }).click(); await page.goto(await securityLink(email, "/verify-email")); await page.getByRole("button", { name: "Verify email" }).click(); await login(page, email, password);
}

async function beginCreatorApplication(page: import("@playwright/test").Page, username: string, displayName: string) {
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByLabel("Username").fill(username); await page.getByLabel("Display name").fill(displayName);
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body.username, { timeout: 15_000 }).toBe(username);
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body.status, { timeout: 15_000 }).toBe("pending_verification");
  await page.getByRole("button", { name: "Complete development verification" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body.status, { timeout: 15_000 }).toBe("pending_review");
}

test("Phase 7 public live uses scoped LiveKit permissions and durable chat", async ({ browser, page }) => {
  const stamp = Date.now(); const password = "phase7-live-password"; const creatorEmail = `phase7-live-${stamp}@example.com`; const username = `live${stamp}`; const liveTitle = `Real stack live ${stamp}`;
  await register(page, creatorEmail, password);
  await beginCreatorApplication(page, username, "Live creator");
  await page.goto("/account"); await page.getByRole("button", { name: "Log out" }).click(); await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications"); const application = applications.body.find((row: { username: string }) => row.username === username); expect(application).toBeTruthy(); expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  await page.getByRole("button", { name: "Log out" }).click(); await login(page, creatorEmail, password); await page.goto("/creator-studio");
  await page.getByLabel("Live title").fill(liveTitle); await page.getByRole("button", { name: "Start live" }).click(); await expect(page.getByText("Live room started with audio and video")).toBeVisible();
  const rooms = await api(page, "/live/rooms"); const room = rooms.body.find((item: { creator_id: string }) => item.creator_id === application.id); expect(room).toBeTruthy();
  const creatorToken = await api(page, `/live/rooms/${room.id}/token`, "POST"); const creatorClaims = JSON.parse(Buffer.from(creatorToken.body.token.split(".")[1], "base64url").toString()); expect(creatorClaims.video.canPublish).toBe(true);
  const viewerContext = await browser.newContext({ permissions: ["camera", "microphone"] }); const viewer = await viewerContext.newPage(); await register(viewer, `phase7-viewer-${stamp}@example.com`, password); await viewer.goto("/live"); await viewer.locator("article", { hasText: liveTitle }).getByRole("button", { name: "Watch live" }).click(); await expect(viewer.getByRole("heading", { name: `Watching: ${liveTitle}` })).toBeVisible(); await expect(viewer.getByLabel("Live video").locator("video")).toBeVisible();
  const viewerToken = await api(viewer, `/live/rooms/${room.id}/token`, "POST"); const viewerClaims = JSON.parse(Buffer.from(viewerToken.body.token.split(".")[1], "base64url").toString()); expect(viewerClaims.video.canPublish).toBe(false); expect(viewerClaims.video.canSubscribe).toBe(true);
  await viewer.getByLabel("Live chat").fill("real live chat"); await viewer.getByRole("button", { name: "Send" }).click(); await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/chat`)).body.map((message: { body: string }) => message.body)).toContain("real live chat"); await viewer.getByRole("button", { name: "Leave live" }).click(); await viewer.locator("article", { hasText: liveTitle }).getByRole("button", { name: "Watch live" }).click(); await expect(viewer.getByText("real live chat")).toBeVisible();
  expect((await api(page, `/live/rooms/${room.id}/reports`, "POST", { reason: "e2e report" })).status).toBe(200);
  await page.getByRole("button", { name: "End public live" }).click(); await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/join`, "POST")).status).toBe(403);
  await viewerContext.close();
});

test("Phase 7 private 1:1 uses signed LiveKit presence and settles once", async ({ browser, page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now(); const password = "phase7-private-password"; const creatorEmail = `phase7-private-${stamp}@example.com`; const viewerEmail = `phase7-private-viewer-${stamp}@example.com`; const username = `private${stamp}`;
  await register(page, creatorEmail, password);
  await beginCreatorApplication(page, username, "Private creator");
  await page.goto("/account"); await page.getByRole("button", { name: "Log out" }).click(); await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications"); const application = applications.body.find((row: { username: string }) => row.username === username); expect(application).toBeTruthy(); expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  await page.getByRole("button", { name: "Log out" }).click(); await login(page, creatorEmail, password); await page.goto("/creator-onboarding"); await page.getByRole("button", { name: "Save profile" }).click(); await page.goto("/creator-studio");
  await page.getByLabel("1:1 per-minute price (minor units)").fill("321"); await page.getByRole("button", { name: "Save private-session pricing" }).click(); await expect(page.getByText("Private-session pricing saved")).toBeVisible();
  await page.getByLabel("Live title").fill(`Private queue live ${stamp}`); await page.getByRole("button", { name: "Start live" }).click(); await expect(page.getByText("Live room started with audio and video")).toBeVisible();
  const viewerContext = await browser.newContext({ permissions: ["camera", "microphone"] }); const viewer = await viewerContext.newPage(); await register(viewer, viewerEmail, password); await viewer.goto(`/creator/${username}`); await viewer.getByRole("button", { name: "Request 1:1 session" }).click(); await expect(viewer.getByText("Request queued")).toBeVisible();
  await page.getByRole("button", { name: "End public live" }).click(); await expect(page.getByText("Live room ended")).toBeVisible(); await page.reload(); await page.getByRole("button", { name: "Accept request" }).click(); await expect(page.getByText("server-side payment authorization is awaiting_payment_authorization")).toBeVisible();
  const creatorSessions = await api(page, "/live/private-sessions/mine"); const session = creatorSessions.body[0]; expect(session).toBeTruthy(); expect(session.status).toBe("awaiting_payment_authorization"); expect(session.per_minute_price_minor).toBe(321);
  await viewer.goto("/live"); await viewer.getByRole("button", { name: "Confirm payment authorization" }).click(); await expect(viewer.getByText("Payment authorization verified")).toBeVisible();
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body[0]?.status).toBe("ready");
  await page.goto("/live"); await page.getByRole("button", { name: "Join private room" }).click(); await expect(page.getByText("Connected to the private room")).toBeVisible(); await viewer.getByRole("button", { name: "Join private room" }).click(); await expect(viewer.getByText("Connected to the private room")).toBeVisible();
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body[0]?.status, { timeout: 15_000 }).toBe("active");
  const payerToken = await api(viewer, `/live/private-sessions/${session.id}/token`, "POST"); const payerClaims = JSON.parse(Buffer.from(payerToken.body.token.split(".")[1], "base64url").toString()); expect(payerClaims.video.roomJoin).toBe(true); expect(payerClaims.video.canPublish).toBe(true);
  // This is a real LiveKit disconnect: no browser callback or API tells the
  // backend that the payer left. The signed provider event must freeze billing.
  await viewerContext.close();
  // LiveKit waits for its configured participant departure detection before
  // emitting the authoritative leave callback. Once that signed callback is
  // received, billing must pause; this deliberately does not use a browser
  // presence API as a shortcut.
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body[0]?.status, { timeout: 30_000 }).toBe("reconnecting");
  const reconnectContext = await browser.newContext({ permissions: ["camera", "microphone"] }); const reconnectingViewer = await reconnectContext.newPage();
  await login(reconnectingViewer, viewerEmail, password); await reconnectingViewer.goto("/live"); await reconnectingViewer.getByRole("button", { name: "Join private room" }).click(); await expect(reconnectingViewer.getByText("Connected to the private room")).toBeVisible();
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body[0]?.status, { timeout: 15_000 }).toBe("active");
  await page.getByRole("button", { name: "End private session" }).click(); await expect(page.getByText("Private session ended")).toBeVisible();
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body.some((item: { id: string }) => item.id === session.id)).toBe(false);
  await reconnectContext.close();
});
