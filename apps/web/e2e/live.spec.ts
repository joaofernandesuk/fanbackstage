import { expect, test } from "@playwright/test";

import { completeCreatorVerification, completeRegistrationCompliance, expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };
const moderator = { email: "phase13-e2e-moderator@example.com", password: "phase13-e2e-moderator-password" };
const liveGiftId = "00000000-0000-4000-8000-000000000047";
const liveTipId = "10000000-0000-4000-8000-000000000002";

async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown, idempotencyKey?: string) {
  const headers: Record<string, string> = {};
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const response = await page.context().request.fetch(`${apiBase}/api/v1${path}`, {
    data: body,
    headers,
    method,
  });
  return { status: response.status(), body: await response.json().catch(() => null) };
}

async function login(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login"); await page.getByLabel("Email").fill(email); await page.getByRole("textbox", { name: /^Password\b/ }).fill(password); await page.getByRole("button", { name: "Log in" }).click(); await expectAuthenticatedAs(page, email);
}

async function register(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/register"); await completeRegistrationCompliance(page); await page.getByLabel("Email").fill(email); await page.getByRole("textbox", { name: /^Password\b/ }).fill(password); await page.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check(); await page.getByRole("button", { name: "Create account" }).click(); await page.goto(await securityLink(email, "/verify-email")); await page.getByRole("button", { name: "Verify email" }).click(); await expect(page.getByText("Email verified", { exact: true })).toBeVisible({ timeout: 15_000 }); await login(page, email, password);
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

async function expectResponsiveLiveChrome(
  page: import("@playwright/test").Page,
  width: number,
  height: number,
) {
  await page.setViewportSize({ width, height });
  const stage = page.getByLabel("Live video");
  const actions = page.getByRole("navigation", { name: "Live actions" });
  const quickTips = page.getByRole("navigation", { name: "Quick tips" });
  const controls = page.getByRole("group", { name: "Video controls" });
  await expect(stage).toBeVisible();
  await expect(actions).toBeVisible();
  await expect(quickTips).toBeVisible();
  await expect(controls).toBeVisible();
  const [stageBox, actionBox, quickTipBox, controlBox] = await Promise.all([
    stage.boundingBox(),
    actions.boundingBox(),
    quickTips.boundingBox(),
    controls.boundingBox(),
  ]);
  expect(stageBox && actionBox && quickTipBox && controlBox).toBeTruthy();
  expect(actionBox!.x).toBeGreaterThanOrEqual(stageBox!.x - 1);
  expect(actionBox!.x + actionBox!.width).toBeLessThanOrEqual(stageBox!.x + stageBox!.width + 1);
  expect(actionBox!.y + actionBox!.height).toBeLessThanOrEqual(quickTipBox!.y + 1);
  expect(controlBox!.y + controlBox!.height).toBeLessThanOrEqual(quickTipBox!.y + 1);
  expect(quickTipBox!.y + quickTipBox!.height).toBeLessThanOrEqual(stageBox!.y + stageBox!.height + 2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
}

test("Phase 7 public live uses scoped LiveKit permissions and durable chat", async ({ browser, page }) => {
  test.setTimeout(300_000);
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
  const creatorStage = page.getByLabel("Your live camera preview"); const endLiveControl = creatorStage.getByRole("button", { name: "End public live" }); await expect(endLiveControl).toBeVisible();
  const rooms = await api(page, "/live/rooms"); const room = rooms.body.find((item: { creator_id: string }) => item.creator_id === application.id); expect(room).toBeTruthy();
  expect((await api(page, "/live/paid-request-options", "POST", { label: "Choose the next song", amount_minor: 700, enabled: true, sort_order: 0, requires_creator_acceptance: true })).status).toBe(200);
  expect((await api(page, "/live/goals", "POST", { title: "First request", target_amount_minor: 700 })).status).toBe(200);
  expect((await api(page, "/live/settings", "PATCH", { snapshots_enabled: true, snapshot_price_minor: 425 })).status).toBe(200);
  const creatorToken = await api(page, `/live/rooms/${room.id}/token`, "POST"); const creatorClaims = JSON.parse(Buffer.from(creatorToken.body.token.split(".")[1], "base64url").toString()); expect(creatorClaims.video.canPublish).toBe(true);
  const viewerContext = await browser.newContext({ permissions: ["camera", "microphone"] }); const viewer = await viewerContext.newPage(); await register(viewer, `phase7-viewer-${stamp}@example.com`, password); await viewer.goto("/live"); await expect(viewer.getByLabel("Live directory filters")).toBeVisible(); const directoryCard = viewer.locator("article", { hasText: liveTitle }); await directoryCard.hover(); await expect(directoryCard.getByText("LIVE PREVIEW", { exact: true })).toBeVisible({ timeout: 20_000 }); await directoryCard.getByRole("button", { name: "Watch live" }).click(); await expect(viewer.getByRole("heading", { name: `Watching: ${liveTitle}` })).toBeVisible({ timeout: 20_000 }); await expect(viewer.getByLabel("Live video").locator("video")).toBeVisible({ timeout: 20_000 });
  const viewerToken = await api(viewer, `/live/rooms/${room.id}/token`, "POST"); const viewerClaims = JSON.parse(Buffer.from(viewerToken.body.token.split(".")[1], "base64url").toString()); expect(viewerClaims.video.canPublish).toBe(false); expect(viewerClaims.video.canSubscribe).toBe(true);
  const liveActions = viewer.getByRole("navigation", { name: "Live actions" }); const quickTips = viewer.getByRole("navigation", { name: "Quick tips" }); await expect(liveActions).toBeVisible(); await expect(quickTips).toBeVisible(); await expect(viewer.locator("aside").getByRole("button", { name: "Send a paid request" })).toHaveCount(0);
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 800 }, { width: 834, height: 1112 }, { width: 390, height: 844 }]) await expectResponsiveLiveChrome(viewer, viewport.width, viewport.height);
  await viewer.setViewportSize({ width: 1280, height: 720 });
  const [actionBox, quickTipBox, viewerStageBox] = await Promise.all([liveActions.boundingBox(), quickTips.boundingBox(), viewer.getByLabel("Live video").boundingBox()]); expect(actionBox && quickTipBox && viewerStageBox).toBeTruthy(); expect(actionBox!.y + actionBox!.height).toBeLessThanOrEqual(quickTipBox!.y + 1); expect(Math.abs(quickTipBox!.y + quickTipBox!.height - (viewerStageBox!.y + viewerStageBox!.height))).toBeLessThanOrEqual(2);
  await expect(liveActions.getByRole("button", { name: "Creator bio" })).toBeVisible(); await expect(liveActions.getByRole("button", { name: "Add to favorites" })).toBeVisible(); await expect(liveActions.getByRole("button", { name: "Subscribe" })).toBeVisible(); await expect(liveActions.getByRole("button", { name: "Take a paid snapshot" })).toBeVisible(); await expect(liveActions.getByRole("button", { name: "Playback settings" })).toBeVisible();
  const quickTip = viewer.getByRole("button", { name: "Choose You look amazing tip for €2.50" }); await quickTip.hover(); await expect(viewer.getByRole("tooltip")).toHaveText("You look amazing · €2.50");
  await viewer.getByLabel("Live chat").fill("real live chat"); await viewer.getByRole("button", { name: "Send", exact: true }).click(); await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/chat`)).body.map((message: { body: string }) => message.body), { timeout: 30_000 }).toContain("real live chat");
  const creatorSummary = page.getByRole("region", { name: "Current Live summary" });
  await expect(creatorSummary).toContainText("Unique viewers1", { timeout: 20_000 });
  await expect(creatorSummary).toContainText(/Fan [A-F0-9]{6}/);
  const creatorChat = page.getByRole("heading", { name: /Creator chat/ }).locator("..");
  await expect(creatorChat).toContainText(/Fan [A-F0-9]{6}/);
  await expect(creatorChat).toContainText("real live chat");
  await viewer.getByRole("button", { name: "Leave live" }).click(); await viewer.locator("article", { hasText: liveTitle }).getByRole("button", { name: "Watch live" }).click(); await expect(viewer.getByRole("region", { name: `Watching ${liveTitle}` })).toHaveAttribute("data-live-state", "recovered", { timeout: 20_000 }); await expect(viewer.getByText("real live chat")).toBeVisible();
  await viewer.getByRole("button", { name: "React", exact: true }).click(); await viewer.getByRole("button", { name: "React Love" }).click(); await expect(viewer.getByLabel("1 live reactions")).toBeVisible(); await expect(viewer.getByLabel("Love 1 total")).toBeVisible(); await expect(page.getByLabel("Love +1 · 1 total")).toBeVisible({ timeout: 10_000 });
  await viewer.getByRole("button", { name: "Choose You look amazing tip for €2.50" }).click(); await expect(viewer.getByRole("dialog", { name: "tip controls" })).toBeVisible(); await expect(viewer.getByRole("combobox", { name: "Tip" })).toHaveValue(liveTipId); await viewer.getByRole("button", { name: "Send tip", exact: true }).click(); await expect(viewer.getByText("You look amazing tip sent successfully.")).toBeVisible({ timeout: 20_000 }); await expect(viewer.getByLabel("You look amazing €2.50")).toBeVisible(); await expect(page.getByLabel("You look amazing €2.50")).toBeVisible({ timeout: 10_000 });
  await viewer.getByRole("button", { name: "Send a gift" }).click(); await expect(viewer.getByRole("dialog", { name: "gift controls" })).toBeVisible(); await viewer.getByRole("combobox", { name: "Gift" }).selectOption(liveGiftId); await viewer.getByRole("button", { name: "Send gift", exact: true }).click(); await expect(viewer.getByText("E2E Rose sent successfully.")).toBeVisible({ timeout: 20_000 }); await expect(viewer.getByLabel("E2E Rose €3.00")).toBeVisible(); await expect(page.getByLabel("E2E Rose €3.00")).toBeVisible({ timeout: 10_000 });
  await viewer.getByRole("button", { name: "Take a paid snapshot" }).click(); await expect(viewer.getByRole("dialog", { name: "snapshot controls" })).toContainText("€4.25"); const downloadPromise = viewer.waitForEvent("download"); await viewer.getByRole("button", { name: "Pay €4.25 and capture" }).click(); const snapshotDownload = await downloadPromise; expect(snapshotDownload.suggestedFilename()).toMatch(/^fanbackstage-live-.*\.png$/); await expect(viewer.getByLabel("Live snapshot €4.25")).toBeVisible({ timeout: 20_000 }); await expect(page.getByLabel("Live snapshot €4.25")).toBeVisible({ timeout: 10_000 });
  await page.getByRole("button", { name: "VIP mode" }).click(); const vipControls = page.getByRole("region", { name: "VIP show controls" }); await expect(vipControls).toBeVisible(); await vipControls.getByLabel("VIP show title").fill("After-hours VIP"); await vipControls.getByLabel("What you promise").fill("A focused paid group show"); await vipControls.getByLabel("VIP funding goal (EUR)").fill("5.00"); await vipControls.getByLabel("Admission price (EUR)").fill("5.00"); await vipControls.getByLabel("Pre-show countdown").selectOption("5"); await vipControls.getByRole("button", { name: "Start VIP pre-show" }).click(); await expect(page.getByText("VIP pre-show started. The promise, goal, buy-in, and timing are now locked.")).toBeVisible();
  await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/vip-show`)).body?.title, { timeout: 20_000 }).toBe("After-hours VIP"); await expect(viewer.getByText("VIP · After-hours VIP")).toBeVisible({ timeout: 20_000 }); await viewer.getByRole("button", { name: "VIP show" }).click(); const vipDialog = viewer.getByRole("dialog", { name: "vip controls" }); await expect(vipDialog).toContainText("After-hours VIP"); await vipDialog.getByRole("button", { name: "Buy VIP admission" }).click(); await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/vip-show`)).body, { timeout: 20_000 }).toMatchObject({ viewer_admitted: true, confirmed_amount_minor: 500 });
  await expect(vipControls.getByRole("button", { name: "Start VIP now" })).toBeEnabled({ timeout: 20_000 }); await vipControls.getByRole("button", { name: "Start VIP now" }).click(); await expect.poll(async () => (await api(page, `/live/rooms/${room.id}/vip-show`)).body.status, { timeout: 20_000 }).toBe("active"); await expect(viewer.getByText("VIP show live", { exact: true }).first()).toBeVisible(); await expect(viewer.getByLabel("A fan joined the VIP show €5.00")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Close live setup" }).click();
  const tipKey = `phase7-tip-${stamp}`; const tip = await api(viewer, `/live/rooms/${room.id}/tips`, "POST", { tip_catalog_item_id: liveTipId }, tipKey); expect(tip.status).toBe(200);
  const tipReplay = await api(viewer, `/live/rooms/${room.id}/tips`, "POST", { tip_catalog_item_id: liveTipId }, tipKey); expect(tipReplay.body.id).toBe(tip.body.id); expect(tipReplay.body.payment_attempt_id).toBe(tip.body.payment_attempt_id);
  expect((await api(viewer, `/payments/development/${tip.body.payment_attempt_id}/complete`, "POST")).status).toBe(200); expect((await api(viewer, `/payments/development/${tip.body.payment_attempt_id}/complete`, "POST")).status).toBe(200);
  const giftKey = `phase7-gift-${stamp}`; const gift = await api(viewer, `/live/rooms/${room.id}/gifts`, "POST", { gift_catalog_item_id: liveGiftId }, giftKey); expect(gift.status).toBe(200);
  const giftReplay = await api(viewer, `/live/rooms/${room.id}/gifts`, "POST", { gift_catalog_item_id: liveGiftId }, giftKey); expect(giftReplay.body.id).toBe(gift.body.id); expect(giftReplay.body.payment_attempt_id).toBe(gift.body.payment_attempt_id);
  expect((await api(viewer, `/payments/development/${gift.body.payment_attempt_id}/complete`, "POST")).status).toBe(200); expect((await api(viewer, `/payments/development/${gift.body.payment_attempt_id}/complete`, "POST")).status).toBe(200);
  await viewer.getByRole("button", { name: "Send a paid request" }).click(); await viewer.getByLabel("Request details").fill("Play the fan favourite"); await viewer.getByRole("button", { name: "Pay and send request" }).click(); await expect(viewer.getByText("Payment confirmed. Your request is waiting for the creator.")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Paid requests" }).click(); await expect(page.getByRole("button", { name: "Accept paid request" })).toBeVisible({ timeout: 20_000 }); const pendingRequests = await api(page, "/live/paid-requests/mine/creator"); const pendingRequest = pendingRequests.body.find((item: { status: string }) => item.status === "paid_pending_creator"); expect(pendingRequest).toBeTruthy(); await page.getByRole("button", { name: "Accept paid request" }).click(); await expect(page.getByText("Paid request accepted and settled once.")).toBeVisible({ timeout: 20_000 }); expect((await api(page, `/live/paid-requests/${pendingRequest.id}/accept`, "POST")).status).toBe(200); await page.getByRole("button", { name: "Close live setup" }).click();
  await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/activity`)).body.map((event: { event_type: string }) => event.event_type), { timeout: 15_000 }).toEqual(expect.arrayContaining(["snapshot", "vip_preshow_started", "vip_admission", "vip_started", "paid_request_pending", "paid_request", "goal_completed"]));
  await expect.poll(async () => { const events = (await api(viewer, `/live/rooms/${room.id}/activity`)).body as { event_type: string }[]; return { tip: events.filter((event) => event.event_type === "tip").length, gift: events.filter((event) => event.event_type === "gift").length, paid_request: events.filter((event) => event.event_type === "paid_request").length }; }, { timeout: 15_000 }).toEqual({ tip: 2, gift: 2, paid_request: 1 });
  await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/supporters`)).body[0]?.amount_minor, { timeout: 15_000 }).toBe(2725);
  await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/goals`)).body[0]?.progress_amount_minor, { timeout: 15_000 }).toBe(2725);
  await viewer.getByRole("button", { name: "Leave live" }).click(); await viewer.locator("article", { hasText: liveTitle }).getByRole("button", { name: "Watch live" }).click();
  await viewer.getByRole("tab", { name: "activity" }).click(); await expect(viewer.getByLabel("Live activity")).toContainText("tip", { timeout: 20_000 }); await expect(viewer.getByLabel("Live activity")).toContainText("gift"); await expect(viewer.getByLabel("Live activity")).toContainText("paid request"); await expect(viewer.getByLabel("Live activity")).toContainText("snapshot"); await viewer.getByRole("tab", { name: "Top" }).click(); await expect(viewer.getByLabel("Top supporters")).toContainText("You"); await expect(viewer.getByLabel("Goal: First request")).toContainText("€27.25"); await viewer.getByRole("button", { name: "React", exact: true }).click(); await expect(viewer.getByRole("button", { name: "React Love" })).toContainText("1");
  const viewerAccount = await api(viewer, "/me"); const report = await api(viewer, `/live/rooms/${room.id}/reports`, "POST", { reason: "harassment", details: "Durable participant-removal E2E report" }); expect(report.status).toBe(200);
  const moderatorContext = await browser.newContext(); const moderatorPage = await moderatorContext.newPage(); await login(moderatorPage, moderator.email, moderator.password);
  await expect.poll(async () => (await api(moderatorPage, "/trust-safety/cases")).body.some((item: { public_id: string }) => item.public_id === report.body.case_id), { timeout: 15_000 }).toBe(true);
  const cases = await api(moderatorPage, "/trust-safety/cases"); const reportCase = cases.body.find((item: { public_id: string }) => item.public_id === report.body.case_id); const enforcement = await api(moderatorPage, `/trust-safety/cases/${reportCase.id}/enforcement`, "POST", { action: "remove_live_participant", target_id: viewerAccount.body.id, reason: "Harassment in the active Live room" }); expect(enforcement.status).toBe(200); expect(enforcement.body.type).toBe("live_participant_remove");
  await expect.poll(async () => viewer.getByLabel("Live video").locator("video").evaluateAll((videos) => videos.every((video) => {
    const stream = (video as HTMLVideoElement).srcObject as MediaStream | null;
    return stream === null || stream.getTracks().every((track) => track.readyState === "ended" || track.muted);
  })), { timeout: 20_000 }).toBe(true); await moderatorContext.close();
  await page.getByRole("button", { name: "End public live" }).click(); await expect(page.getByRole("dialog", { name: "Confirm end Live" })).toBeVisible(); await page.getByRole("button", { name: "End Live now" }).click(); await expect(page.getByText("Ending live for everyone…")).toBeVisible(); await expect(page.getByRole("button", { name: "Start live" })).toBeVisible({ timeout: 20_000 }); await expect(page.getByText("Live room ended. You can now accept queued private requests.")).toBeVisible({ timeout: 20_000 }); await expect.poll(async () => (await api(viewer, `/live/rooms/${room.id}/join`, "POST")).status, { timeout: 20_000 }).toBe(403);
  await expect(viewer.getByRole("region", { name: "Creator is offline" })).toContainText("Live creator is offline", { timeout: 25_000 });
  await expect(viewer.getByRole("button", { name: "Browse live creators" })).toBeVisible();
  await viewerContext.close();
});

test("Phase 7 private 1:1 pauses public Live, supports paid peeks, and resumes", async ({ browser, page }) => {
  test.setTimeout(300_000);
  const stamp = Date.now(); const password = "phase7-private-password"; const creatorEmail = `phase7-private-${stamp}@example.com`; const viewerEmail = `phase7-private-viewer-${stamp}@example.com`; const username = `private${stamp}`; const liveTitle = `Private pause live ${stamp}`;
  await register(page, creatorEmail, password);
  await beginCreatorApplication(page, username, "Private creator");
  await page.goto("/account"); await page.getByRole("button", { name: "Log out" }).click(); await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications"); const application = applications.body.find((row: { username: string }) => row.username === username); expect(application).toBeTruthy(); expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  expect((await api(page, "/live/admin/private-peek-policy", "PUT", { active: true, amount_minor: 275, currency: "EUR", commission_basis_points: 2400, reason: "Private Live E2E policy", confirmed: true })).status).toBe(200);
  await page.getByRole("button", { name: "Log out" }).click(); await login(page, creatorEmail, password); await page.goto("/creator-onboarding"); await page.getByRole("checkbox", { name: "Make my approved creator profile public" }).check(); await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body, { timeout: 15_000 }).toMatchObject({ status: "approved", is_public: true });
  await page.goto("/creator-studio"); await page.getByRole("button", { name: "Go live" }).click();
  await page.getByRole("button", { name: /Private sessions Pricing/ }).click(); await page.getByLabel("1:1 price per minute (EUR)").fill("3.21"); await page.getByLabel("Allow paid, view-only peeks during future private sessions").check(); await page.getByRole("button", { name: "Save private-session pricing" }).click(); await expect(page.getByText("Private-session pricing saved")).toBeVisible();
  await page.getByLabel("Live title").fill(liveTitle); await page.getByRole("button", { name: "Start live" }).click(); await expect(page.getByText("You are live. Your camera, microphone, and creator chat are ready.")).toBeVisible({ timeout: 20_000 });
  const room = (await api(page, "/live/rooms")).body.find((item: { creator_id: string }) => item.creator_id === application.id); expect(room).toBeTruthy();

  const viewerContext = await browser.newContext({ permissions: ["camera", "microphone"] }); const viewer = await viewerContext.newPage(); await register(viewer, viewerEmail, password); await viewer.goto("/live"); await viewer.locator("article", { hasText: liveTitle }).getByRole("button", { name: "Watch live" }).click(); await expect(viewer.getByLabel("Live video").locator("video")).toBeVisible({ timeout: 20_000 });
  await viewer.getByRole("button", { name: "Request a private session" }).click(); await viewer.getByRole("button", { name: "Request private 1:1" }).click();
  const creatorRequestAlert = page.getByRole("alertdialog", { name: "New private session request" }); await expect(creatorRequestAlert).toBeVisible({ timeout: 15_000 }); await expect(creatorRequestAlert).toContainText("€3.21/minute"); await expect(creatorRequestAlert).toContainText("does not end this public show"); await creatorRequestAlert.getByRole("button", { name: "Accept private" }).click();
  await expect(viewer.getByRole("dialog", { name: "Authorize private session" })).toBeVisible({ timeout: 20_000 }); await expect(viewer.getByRole("dialog", { name: "Authorize private session" })).toContainText("silent, view-only peek access");
  const session = (await api(page, "/live/private-sessions/mine")).body[0]; expect(session.status).toBe("awaiting_payment_authorization"); expect(session.peeks_allowed).toBe(true);
  await viewer.getByRole("button", { name: "Authorize payment and enter private" }).click();
  await expect(viewer.getByText("YOUR PRIVATE SESSION", { exact: true })).toBeVisible({ timeout: 30_000 }); await expect(page.getByText("You are in private", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body[0]?.status, { timeout: 30_000 }).toBe("active");
  await expect.poll(async () => (await api(page, "/live/rooms")).body.find((item: { id: string }) => item.id === room.id)?.private_paused, { timeout: 20_000 }).toBe(true);
  const payerToken = await api(viewer, `/live/private-sessions/${session.id}/token`, "POST"); const payerClaims = JSON.parse(Buffer.from(payerToken.body.token.split(".")[1], "base64url").toString()); expect(payerClaims.video.canPublish).toBe(true);

  const peekerContext = await browser.newContext(); const peeker = await peekerContext.newPage(); await register(peeker, `phase7-peeker-${stamp}@example.com`, password); await peeker.goto("/live"); await peeker.locator("article", { hasText: liveTitle }).getByRole("button", { name: "Watch live" }).click(); await expect(peeker.getByLabel("Private session holding screen")).toBeVisible({ timeout: 20_000 }); await expect(peeker.getByRole("button", { name: "Take a peek · €2.75" })).toBeVisible(); await peeker.getByRole("button", { name: "Take a peek · €2.75" }).click(); await expect(peeker.getByText("PAID PEEK · VIEW ONLY", { exact: true })).toBeVisible({ timeout: 20_000 });
  const peekToken = await api(peeker, `/live/rooms/${room.id}/private-peek/token`, "POST"); const peekClaims = JSON.parse(Buffer.from(peekToken.body.token.split(".")[1], "base64url").toString()); expect(peekClaims.video.canPublish).toBe(false); expect(peekClaims.video.canPublishData).toBe(false); expect(peekClaims.video.canSubscribe).toBe(true);

  await page.getByRole("button", { name: "End private and resume public" }).click();
  await expect.poll(async () => (await api(page, "/live/rooms")).body.find((item: { id: string }) => item.id === room.id)?.private_paused, { timeout: 30_000 }).toBe(false);
  await expect(page.getByText("Private session ended. Your public Live has resumed.")).toBeVisible({ timeout: 30_000 }); await expect(viewer.getByLabel("Live video").locator("video")).toBeVisible({ timeout: 30_000 }); await expect(peeker.getByLabel("Live video").locator("video")).toBeVisible({ timeout: 30_000 });
  await expect.poll(async () => (await api(page, "/live/private-sessions/mine")).body.some((item: { id: string }) => item.id === session.id), { timeout: 20_000 }).toBe(false);
  await viewerContext.close(); await peekerContext.close();
});
