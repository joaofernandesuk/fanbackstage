import { expect, test } from "@playwright/test";

import { completeCreatorVerification, completeRegistrationCompliance, expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };
const moderator = { email: "phase13-e2e-moderator@example.com", password: "phase13-e2e-moderator-password" };
const liveGiftId = "00000000-0000-4000-8000-000000000047";

async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown, idempotencyKey?: string) {
  return page.evaluate(async ({ apiBase, path, method, body, idempotencyKey }) => {
    const headers: Record<string, string> = {};
    if (body) headers["Content-Type"] = "application/json";
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const response = await fetch(`${apiBase}/api/v1${path}`, { method, credentials: "include", headers, body: body ? JSON.stringify(body) : undefined });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { apiBase, path, method, body, idempotencyKey });
}

async function login(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login"); await page.getByLabel("Email").fill(email); await page.getByRole("textbox", { name: /^Password\b/ }).fill(password); await page.getByRole("button", { name: "Log in" }).click(); await expectAuthenticatedAs(page, email);
}

async function register(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/register"); await completeRegistrationCompliance(page); await page.getByLabel("Email").fill(email); await page.getByRole("textbox", { name: /^Password\b/ }).fill(password); await page.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check(); await page.getByRole("button", { name: "Create account" }).click(); await page.goto(await securityLink(email, "/verify-email")); await page.getByRole("button", { name: "Verify email" }).click(); await login(page, email, password);
}

async function beginCreatorApplication(page: import("@playwright/test").Page, username: string, displayName: string) {
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByRole("textbox", { name: /^Your @handle/ }).fill(username); await page.getByLabel("Display name").fill(displayName);
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body.username, { timeout: 15_000 }).toBe(username);
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body.status, { timeout: 15_000 }).toBe("pending_verification");
  await completeCreatorVerification(page);
}

test("Phase 7 public live uses scoped LiveKit permissions and durable chat", async ({ browser, page }) => {
  test.setTimeout(180_000);
  const stamp = Date.now(); const password = "phase7-live-password"; const creatorEmail = `phase7-live-${stamp}@example.com`; const username = `live${stamp}`; const liveTitle = `Real stack live ${stamp}`;
  await register(page, creatorEmail, password);
  await beginCreatorApplication(page, username, "Live creator");
  await page.goto("/account"); await page.getByRole("button", { name: "Log out" }).click(); await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications"); const application = applications.body.find((row: { username: string }) => row.username === username); expect(application).toBeTruthy(); expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  await page.getByRole("button", { name: "Log out" }).click(); await login(page, creatorEmail, password); await page.goto("/creator-onboarding"); await page.getByRole("checkbox", { name: "Make my approved creator profile public" }).check(); await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body, { timeout: 15_000 }).toMatchObject({ status: "approved", is_public: true });
  await page.goto("/creator-studio");
  await page.getByRole("button", { name: "Go live" }).click();
  await page.getByLabel("Live title").fill(liveTitle); await page.getByRole("button", { name: "Start live" }).click(); await expect(page.getByText("You are live. Your camera, microphone, and creator chat are ready.")).toBeVisible({ timeout: 20_000 });
  const rooms = await api(page, "/live/rooms"); const room = rooms.body.find((item: { creator_id: string }) => item.creator_id === application.id); expect(room).toBeTruthy();
  expect((await api(page, "/live/paid-request-options", "POST", { label: "Choose the next song", amount_minor: 700, enabled: true, sort_order: 0, requires_creator_acceptance: true })).status).toBe(200);
  expect((await api(page, "/live/goals", "POST", { title: "First request", target_amount_minor: 700 })).status).toBe(200);
  const creatorToken = await api(page, `/live/rooms/${room.id}/token`, "POST"); const creatorClaims = JSON.parse(Buffer.from(creatorToken.body.token.split(".")[1], "base64url").toString()); expect(creatorClaims.video.canPublish).toBe(true);
  const viewerContext = await browser.newContext({ permissions: ["camera", "microphone"] }); const viewer = await viewerContext.newPage(); await register(viewer, `phase7-viewer-${stamp}@example.com`, password); await viewer.goto("/live"); await viewer.locator("article", { hasText: liveTitle }).getByRole("button", { name: "Watch live" }).click(); await expect(viewer.getByRole("heading", { name: `Watching: ${liveTitle}` })).toBeVisible(); await expect(viewer.getByLabel("Live video").locator("video")).toBeVisible();
  const viewerToken = await api(viewer, `/live/rooms/${room.id}/token`, "POST"); const viewerClaims = JSON.parse(Buffer.from(viewerToken.body.token.split(".")[1], "base64url").toString()); expect(viewerClaims.video.canPublish).toBe(false); expect(viewerClaims.video.canSubscribe).toBe(true);
  await viewer.getByLabel("Live chat").fill("real live chat"); await viewer.getByRole("button", { name: "Send", exact: true }).click(); await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/chat`)).body.map((message: { body: string }) => message.body)).toContain("real live chat"); await viewer.getByRole("button", { name: "Leave live" }).click(); await viewer.locator("article", { hasText: liveTitle }).getByRole("button", { name: "Watch live" }).click(); await expect(viewer.getByText("real live chat")).toBeVisible();
  await viewer.getByRole("button", { name: "React Love" }).click();
  const tipKey = `phase7-tip-${stamp}`; const tip = await api(viewer, `/live/rooms/${room.id}/tips`, "POST", { amount_minor: 250 }, tipKey); expect(tip.status).toBe(200);
  const tipReplay = await api(viewer, `/live/rooms/${room.id}/tips`, "POST", { amount_minor: 250 }, tipKey); expect(tipReplay.body.id).toBe(tip.body.id); expect(tipReplay.body.payment_attempt_id).toBe(tip.body.payment_attempt_id);
  expect((await api(viewer, `/payments/development/${tip.body.payment_attempt_id}/complete`, "POST")).status).toBe(200); expect((await api(viewer, `/payments/development/${tip.body.payment_attempt_id}/complete`, "POST")).status).toBe(200);
  const giftKey = `phase7-gift-${stamp}`; const gift = await api(viewer, `/live/rooms/${room.id}/gifts`, "POST", { gift_catalog_item_id: liveGiftId }, giftKey); expect(gift.status).toBe(200);
  const giftReplay = await api(viewer, `/live/rooms/${room.id}/gifts`, "POST", { gift_catalog_item_id: liveGiftId }, giftKey); expect(giftReplay.body.id).toBe(gift.body.id); expect(giftReplay.body.payment_attempt_id).toBe(gift.body.payment_attempt_id);
  expect((await api(viewer, `/payments/development/${gift.body.payment_attempt_id}/complete`, "POST")).status).toBe(200); expect((await api(viewer, `/payments/development/${gift.body.payment_attempt_id}/complete`, "POST")).status).toBe(200);
  await viewer.getByLabel("Request details").fill("Play the fan favourite"); await viewer.getByRole("button", { name: "Pay and send request" }).click(); await expect(viewer.getByText("Payment confirmed. Your request is waiting for the creator.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Accept paid request" })).toBeVisible({ timeout: 15_000 }); const pendingRequests = await api(page, "/live/paid-requests/mine/creator"); const pendingRequest = pendingRequests.body.find((item: { status: string }) => item.status === "paid_pending_creator"); expect(pendingRequest).toBeTruthy(); await page.getByRole("button", { name: "Accept paid request" }).click(); await expect(page.getByText("Paid request accepted and settled once.")).toBeVisible(); expect((await api(page, `/live/paid-requests/${pendingRequest.id}/accept`, "POST")).status).toBe(200);
  await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/activity`)).body.map((event: { event_type: string }) => event.event_type), { timeout: 15_000 }).toEqual(expect.arrayContaining(["paid_request_pending", "paid_request", "goal_completed"]));
  await expect.poll(async () => { const events = (await api(viewer, `/live/rooms/${room.id}/activity`)).body as { event_type: string }[]; return { tip: events.filter((event) => event.event_type === "tip").length, gift: events.filter((event) => event.event_type === "gift").length, paid_request: events.filter((event) => event.event_type === "paid_request").length }; }, { timeout: 15_000 }).toEqual({ tip: 1, gift: 1, paid_request: 1 });
  await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/supporters`)).body[0]?.amount_minor, { timeout: 15_000 }).toBe(1250);
  await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/goals`)).body[0]?.progress_amount_minor, { timeout: 15_000 }).toBe(1250);
  await viewer.getByRole("button", { name: "Leave live" }).click(); await viewer.locator("article", { hasText: liveTitle }).getByRole("button", { name: "Watch live" }).click();
  await expect(viewer.getByLabel("Live activity")).toContainText("tip"); await expect(viewer.getByLabel("Live activity")).toContainText("gift"); await expect(viewer.getByLabel("Live activity")).toContainText("paid request"); await expect(viewer.getByLabel("Top supporters")).toContainText("You"); await expect(viewer.getByLabel("Goal: First request")).toContainText("€12.50"); await expect(viewer.getByRole("button", { name: "React Love" })).toContainText("1");
  const viewerAccount = await api(viewer, "/me"); const report = await api(viewer, `/live/rooms/${room.id}/reports`, "POST", { reason: "harassment", details: "Durable participant-removal E2E report" }); expect(report.status).toBe(200);
  const moderatorContext = await browser.newContext(); const moderatorPage = await moderatorContext.newPage(); await login(moderatorPage, moderator.email, moderator.password);
  await expect.poll(async () => (await api(moderatorPage, "/trust-safety/cases")).body.some((item: { public_id: string }) => item.public_id === report.body.case_id), { timeout: 15_000 }).toBe(true);
  const cases = await api(moderatorPage, "/trust-safety/cases"); const reportCase = cases.body.find((item: { public_id: string }) => item.public_id === report.body.case_id); const enforcement = await api(moderatorPage, `/trust-safety/cases/${reportCase.id}/enforcement`, "POST", { action: "remove_live_participant", target_id: viewerAccount.body.id, reason: "Harassment in the active Live room" }); expect(enforcement.status).toBe(200); expect(enforcement.body.type).toBe("live_participant_remove");
  await expect.poll(async () => viewer.getByLabel("Live video").locator("video").evaluateAll((videos) => videos.every((video) => {
    const stream = (video as HTMLVideoElement).srcObject as MediaStream | null;
    return stream === null || stream.getTracks().every((track) => track.readyState === "ended" || track.muted);
  })), { timeout: 20_000 }).toBe(true); await moderatorContext.close();
  await page.getByRole("button", { name: "End public live" }).click(); await expect(page.getByText("Ending live for everyone…")).toBeVisible(); await expect(page.getByRole("button", { name: "Start live" })).toBeVisible({ timeout: 20_000 }); await expect(page.getByText("Live room ended. You can now accept queued private requests.")).toBeVisible(); await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/join`, "POST")).status).toBe(403);
  await viewerContext.close();
});

test("Phase 7 private 1:1 uses signed LiveKit presence and settles once", async ({ browser, page }) => {
  test.setTimeout(120_000);
  const stamp = Date.now(); const password = "phase7-private-password"; const creatorEmail = `phase7-private-${stamp}@example.com`; const viewerEmail = `phase7-private-viewer-${stamp}@example.com`; const username = `private${stamp}`;
  await register(page, creatorEmail, password);
  await beginCreatorApplication(page, username, "Private creator");
  await page.goto("/account"); await page.getByRole("button", { name: "Log out" }).click(); await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications"); const application = applications.body.find((row: { username: string }) => row.username === username); expect(application).toBeTruthy(); expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  await page.getByRole("button", { name: "Log out" }).click(); await login(page, creatorEmail, password); await page.goto("/creator-onboarding"); await page.getByRole("checkbox", { name: "Make my approved creator profile public" }).check(); await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body, { timeout: 15_000 }).toMatchObject({ status: "approved", is_public: true });
  await page.goto("/creator-studio");
  await page.getByRole("button", { name: "Go live" }).click();
  await page.getByLabel("1:1 per-minute price (minor units)").fill("321"); await page.getByRole("button", { name: "Save private-session pricing" }).click(); await expect(page.getByText("Private-session pricing saved")).toBeVisible();
  await page.getByLabel("Live title").fill(`Private queue live ${stamp}`); await page.getByRole("button", { name: "Start live" }).click(); await expect(page.getByText("You are live. Your camera, microphone, and creator chat are ready.")).toBeVisible({ timeout: 20_000 });
  const viewerContext = await browser.newContext({ permissions: ["camera", "microphone"] }); const viewer = await viewerContext.newPage();
  // The public creator page is age-gated before its login call-to-action.
  // Establish the viewer's current registration/legal/age authority first so
  // this private-session lifecycle test exercises LiveKit rather than a
  // separate anonymous access gate.
  await register(viewer, viewerEmail, password); await viewer.goto(`/creator/${username}`); await viewer.getByRole("button", { name: "Request 1:1 session" }).click(); await expect(viewer.getByText("Request queued")).toBeVisible();
  await page.getByRole("button", { name: "End public live" }).click(); await expect(page.getByText("Live room ended")).toBeVisible({ timeout: 20_000 }); await page.reload();
  // Ending first commits a durable provider-control intent. Poll the
  // authoritative acceptance command rather than a transient Studio message:
  // it becomes valid only once the outbox finalizer has closed the public
  // room, and stops immediately after the one successful mutation.
  await expect.poll(
    async () => (await api(page, `/live/private-requests/${(await api(page, "/live/private-requests/mine/creator")).body[0]?.id}/accept`, "POST")).status,
    { timeout: 15_000 },
  ).toBe(200);
  const creatorSessions = await api(page, "/live/private-sessions/mine"); const session = creatorSessions.body[0]; expect(session).toBeTruthy(); expect(session.status).toBe("awaiting_payment_authorization"); expect(session.per_minute_price_minor).toBe(321);
  await viewer.goto("/live"); await viewer.getByRole("button", { name: "Confirm payment authorization" }).click(); await expect(viewer.getByText("Payment authorization verified")).toBeVisible();
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body[0]?.status).toBe("ready");
  await page.goto("/live"); await page.getByRole("button", { name: "Join private room" }).click(); await expect(page.getByText("Connected to the private room")).toBeVisible(); await viewer.getByRole("button", { name: "Join private room" }).click(); await expect(viewer.getByText("Connected to the private room")).toBeVisible();
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body[0]?.status, { timeout: 15_000 }).toBe("active");
  const payerToken = await api(viewer, `/live/private-sessions/${session.id}/token`, "POST"); const payerClaims = JSON.parse(Buffer.from(payerToken.body.token.split(".")[1], "base64url").toString()); expect(payerClaims.video.roomJoin).toBe(true); expect(payerClaims.video.canPublish).toBe(true);
  // Keep the browser context alive while the LiveKit component unmounts so its
  // asynchronous SDK disconnect can finish. Closing the context directly can
  // terminate the page before LiveKit sends its leave handshake. The backend
  // still learns about departure only from the signed provider event.
  await viewer.getByRole("link", { name: "Home", exact: true }).click();
  await expect(viewer).toHaveURL(/\/feed(?:[?#].*)?$/);
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body[0]?.status, { timeout: 30_000 }).toBe("reconnecting");
  await viewerContext.close();
  const reconnectContext = await browser.newContext({ permissions: ["camera", "microphone"] }); const reconnectingViewer = await reconnectContext.newPage();
  await login(reconnectingViewer, viewerEmail, password); await reconnectingViewer.goto("/live"); await reconnectingViewer.getByRole("button", { name: "Join private room" }).click(); await expect(reconnectingViewer.getByText("Connected to the private room")).toBeVisible();
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body[0]?.status, { timeout: 15_000 }).toBe("active");
  await page.getByRole("button", { name: "End private session" }).click(); await expect(page.getByText("Private session ended")).toBeVisible();
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body.some((item: { id: string }) => item.id === session.id)).toBe(false);
  await reconnectContext.close();
});
