import { expect, test } from "@playwright/test";

import { expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };

async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown, key?: string) {
  return page.evaluate(async ({ apiBase, path, method, body, key }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, { method, credentials: "include", headers: { ...(body ? { "Content-Type": "application/json" } : {}), ...(key ? { "Idempotency-Key": key } : {}) }, body: body ? JSON.stringify(body) : undefined });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { apiBase, path, method, body, key });
}

async function login(page: import("@playwright/test").Page, email: string, password: string) { await page.goto("/login"); await page.getByLabel("Email").fill(email); await page.getByRole("textbox", { name: /^Password\b/ }).fill(password); await page.getByRole("button", { name: "Log in" }).click(); await expectAuthenticatedAs(page, email); }
async function logout(page: import("@playwright/test").Page) { await page.goto("/account"); await page.getByRole("button", { name: "Log out" }).click(); }
async function register(page: import("@playwright/test").Page, email: string, password: string) { await page.goto("/register"); await page.getByLabel("Email").fill(email); await page.getByRole("textbox", { name: /^Password\b/ }).fill(password); await page.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check(); await page.getByRole("button", { name: "Create account" }).click(); await page.goto(await securityLink(email, "/verify-email")); await page.getByRole("button", { name: "Verify email" }).click(); await login(page, email, password); }

test("Phase 6 messaging uses the real payment, inbox, campaign, and moderation stack", async ({ browser, page }) => {
  const stamp = Date.now(); const password = "phase6-messaging-password"; const creatorEmail = `phase6-creator-${stamp}@example.com`; const viewerEmail = `phase6-viewer-${stamp}@example.com`; const username = `message${stamp}`;
  await register(page, creatorEmail, password); await page.getByRole("link", { name: "Become a creator" }).click(); await page.getByLabel("Username").fill(username); await page.getByLabel("Display name").fill("Messaging creator"); await page.getByRole("button", { name: "Save profile" }).click(); await page.getByRole("button", { name: "Submit application" }).click(); await page.getByRole("button", { name: "Complete development verification" }).click(); await logout(page);
  await login(page, admin.email, admin.password); const applications = await api(page, "/admin/creator-applications"); const application = applications.body.find((item: { username: string }) => item.username === username); expect(application).toBeTruthy(); expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200); await logout(page);
  await login(page, creatorEmail, password); await page.goto("/creator-onboarding"); await page.getByRole("checkbox", { name: "Make my approved creator profile public" }).check(); await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body, { timeout: 15_000 }).toMatchObject({ status: "approved", is_public: true });
  const creator = (await api(page, "/creators/me")).body; expect((await api(page, "/messages/settings", "PUT", { permission: "anyone", subscribers_free: true })).status).toBe(200); await logout(page);
  const viewerContext = await browser.newContext(); const viewer = await viewerContext.newPage(); await register(viewer, viewerEmail, password);
  expect((await api(viewer, `/messages/creator/${creator.id}/send-price`)).body).toMatchObject({ amount_minor: null, requires_confirmation: false });
  const free = await api(viewer, `/messages/creator/${creator.id}`, "POST", { body: "free hello" }); expect(free.status).toBe(200); const conversationId = free.body.conversation_id;
  await login(page, creatorEmail, password); expect((await api(page, `/messages/conversations/${conversationId}`)).body.map((item: { body: string }) => item.body)).toContain("free hello"); expect((await api(page, `/messages/conversations/${conversationId}`, "POST", { body: "creator reply" })).status).toBe(200); await viewer.goto("/messages"); await expect(viewer.getByRole("button", { name: /Conversation/ }).first()).toBeVisible(); await viewer.getByRole("button", { name: /Conversation/ }).first().click(); await expect(viewer.getByText("creator reply")).toBeVisible();
  await login(page, creatorEmail, password); expect((await api(page, "/messages/settings", "PUT", { permission: "anyone", send_fee_minor: 250, send_fee_currency: "EUR", subscribers_free: false })).status).toBe(200); await logout(page); await login(viewer, viewerEmail, password);
  expect((await api(viewer, `/messages/creator/${creator.id}/send-price`)).body).toMatchObject({ amount_minor: 250, currency: "EUR", requires_confirmation: true });
  const paid = await api(viewer, `/messages/creator/${creator.id}/paid-send`, "POST", { body: "paid hello" }, "phase6-paid-send"); expect(paid.status).toBe(200); expect((await api(viewer, `/messages/conversations/${conversationId}`)).body.map((item: { body: string }) => item.body)).not.toContain("paid hello"); expect((await api(viewer, `/payments/development/${paid.body.payment_attempt_id}/complete`, "POST")).status).toBe(200); expect((await api(viewer, `/messages/creator/${creator.id}/paid-send`, "POST", { body: "tampered" }, "phase6-paid-send")).body.id).toBe(paid.body.id); expect((await api(viewer, `/messages/conversations/${conversationId}`)).body.filter((item: { body: string }) => item.body === "paid hello")).toHaveLength(1);
  expect((await api(viewer, `/messages/messages/${free.body.id}/report`, "POST", { reason: "test" })).status).toBe(200);
  await viewerContext.close();
});
