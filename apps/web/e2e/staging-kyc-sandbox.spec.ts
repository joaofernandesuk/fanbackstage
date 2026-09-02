import { createHmac } from "node:crypto";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { completeRegistrationCompliance, expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const secret = process.env.FANBACKSTAGE_STAGING_KYC_WEBHOOK_SECRET
  ?? "fanbackstage-e2e-staging-kyc-webhook-secret";
const operator = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };

type ApiResponse = { status: number; body: Record<string, unknown> | null };
type CreatorSelf = {
  id: string;
  status: string;
  verification_status: string;
  staging_kyc_session_reference: string | null;
  staging_kyc_verification_id: string | null;
};

async function providerWebhook(
  request: APIRequestContext,
  payload: string,
  signature: string,
) {
  return request.post(`${apiBase}/api/v1/creators/webhooks/staging-sandbox`, {
    headers: { "Content-Type": "application/json", "X-Kyc-Signature": signature },
    data: payload,
  });
}

async function api(page: Page, path: string, method = "GET", body?: unknown): Promise<ApiResponse> {
  return page.evaluate(async ({ apiBase, path, method, body }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, {
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { apiBase, path, method, body });
}

async function registerCreator(page: Page, stamp: number) {
  const email = `staging-kyc-${stamp}@example.com`;
  const password = "staging-kyc-e2e-password";
  const username = `stgkyc${stamp}`;
  await page.goto("/register");
  await completeRegistrationCompliance(page);
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check();
  await page.getByRole("button", { name: "Create account" }).click();
  await page.goto(await securityLink(email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByRole("textbox", { name: /^Your @handle/ }).fill(username);
  await page.getByLabel("Display name").fill("Staging KYC Creator");
  await page.getByRole("button", { name: "Save profile" }).click();
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body?.status).toBe("pending_verification");
}

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);
}

async function logout(page: Page) {
  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();
}

async function start(page: Page): Promise<CreatorSelf> {
  expect((await api(page, "/creators/me/verification/staging-sandbox/start", "POST")).status).toBe(200);
  const current = (await api(page, "/creators/me")).body as unknown as CreatorSelf;
  expect(current.staging_kyc_verification_id).toBeTruthy();
  expect(current.staging_kyc_session_reference).toBeTruthy();
  return current;
}

test("staging creator KYC uses signed asynchronous callbacks without browser shortcuts", async ({ page, request }) => {
  test.skip(process.env.FANBACKSTAGE_KYC_PROVIDER !== "staging_sandbox", "sandbox adapter gate");
  const stamp = Date.now();
  await registerCreator(page, stamp);

  const failed = await start(page);
  expect((await api(page, `/creators/me/verification/staging-sandbox/${failed.staging_kyc_verification_id}/complete`, "POST", { outcome: "FAILED" })).status).toBe(202);
  await expect.poll(async () => (await api(page, "/creators/me")).body?.verification_status).toBe("failed");

  const review = await start(page);
  expect((await api(page, `/creators/me/verification/staging-sandbox/${review.staging_kyc_verification_id}/complete`, "POST", { outcome: "REVIEW_REQUIRED" })).status).toBe(202);
  await expect.poll(async () => (await api(page, "/creators/me")).body?.verification_status).toBe("needs_review");

  await logout(page);
  await login(page, operator.email, operator.password);
  await page.goto("/admin/creator-kyc");
  await page.getByRole("button", { name: /Staging KYC Creator/ }).click();
  await page.getByLabel("Review reason").fill("Provider requested a fresh identity capture.");
  await page.getByRole("checkbox", { name: /confirm this audited outcome/ }).check();
  await page.getByRole("button", { name: "Record review outcome" }).click();
  await expect(page.getByRole("status")).toContainText("recorded");
  await logout(page);
  await login(page, `staging-kyc-${stamp}@example.com`, "staging-kyc-e2e-password");
  await expect.poll(async () => (await api(page, "/creators/me")).body?.verification_status).toBe("failed");

  const verified = await start(page);
  expect((await api(page, "/creators/me/verification/staging-sandbox/00000000-0000-0000-0000-000000000000/complete", "POST", { outcome: "VERIFIED" })).status).toBe(404);
  const completion = await api(page, `/creators/me/verification/staging-sandbox/${verified.staging_kyc_verification_id}/complete`, "POST", { outcome: "VERIFIED" });
  expect(completion.status).toBe(202);
  const eventId = completion.body?.event_id;
  expect(typeof eventId).toBe("string");
  await expect.poll(async () => (await api(page, "/creators/me")).body?.status).toBe("pending_review");

  const payload = JSON.stringify({
    id: eventId,
    type: "kyc.verified",
    provider_reference: verified.staging_kyc_session_reference,
  });
  const validSignature = createHmac("sha256", secret).update(payload).digest("hex");
  expect((await providerWebhook(request, payload, validSignature)).status()).toBe(204);
  expect((await providerWebhook(request, payload, "invalid")).status()).toBe(400);

  const wrongSubjectPayload = JSON.stringify({
    id: `wrong-${stamp}`,
    type: "kyc.verified",
    provider_reference: "stgkyc_not_the_subject",
  });
  const wrongSubjectSignature = createHmac("sha256", secret).update(wrongSubjectPayload).digest("hex");
  expect((await providerWebhook(request, wrongSubjectPayload, wrongSubjectSignature)).status()).toBe(204);
  expect((await api(page, "/creators/me")).body).toMatchObject({
    status: "pending_review",
    verification_status: "verified",
  });
});
