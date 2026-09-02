import { createHmac } from "node:crypto";

import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { completeRegistrationCompliance, expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const creator = {
  email: "consumer-e2e-creator@example.com",
  password: "consumer-e2e-creator-password",
};
const sandboxSecret =
  process.env.FANBACKSTAGE_STAGING_PAYMENT_WEBHOOK_SECRET
  ?? "fanbackstage-e2e-staging-payment-webhook-secret";

async function api(page: Page, path: string, method = "GET", body?: unknown, headers?: Record<string, string>) {
  return page.evaluate(async ({ apiBase, path, method, body, headers }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, {
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json", ...headers } : headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { apiBase, path, method, body, headers });
}

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);
}

async function register(page: Page, email: string, password: string) {
  await page.goto("/register");
  await completeRegistrationCompliance(page);
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check();
  await page.getByRole("button", { name: "Create account" }).click();
  await page.goto(await securityLink(email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await login(page, email, password);
}

async function signedWebhook(
  request: APIRequestContext,
  payload: Record<string, string>,
  signature = createHmac("sha256", sandboxSecret).update(JSON.stringify(payload)).digest("hex"),
) {
  return request.post(`${apiBase}/api/v1/payments/webhooks/staging-sandbox`, {
    data: JSON.stringify(payload),
    headers: {
      "Content-Type": "application/json",
      "X-Payment-Signature": signature,
    },
  });
}

test("staging payment sandbox settles subscription retry only through signed callbacks", async ({ page, request }) => {
  test.skip(process.env.FANBACKSTAGE_PAYMENT_PROVIDER !== "staging_sandbox", "sandbox adapter gate");
  const stamp = Date.now();
  const password = "staging-payment-sandbox-password";
  const buyerEmail = `staging-payment-${stamp}@example.com`;

  await login(page, creator.email, creator.password);
  const creatorSelf = await api(page, "/creators/me");
  expect(creatorSelf.status).toBe(200);
  expect((await api(page, "/creator/subscription-plan", "PUT", {
    currency: "EUR",
    enabled: true,
    prices: [{ duration: "month_1", amount_minor: 1000, enabled: true }],
  })).status).toBe(200);
  await page.getByRole("button", { name: "Log out" }).click();

  await register(page, buyerEmail, password);
  const first = await api(
    page,
    `/subscriptions/creator/${creatorSelf.body.id}`,
    "POST",
    { duration: "month_1" },
    { "Idempotency-Key": `staging-decline-${stamp}` },
  );
  expect(first.status, JSON.stringify(first.body)).toBe(200);
  expect(first.body.payment_attempt_id).toBeTruthy();
  const firstCheckout = await api(page, `/payments/${first.body.payment_attempt_id}/checkout`);
  expect(firstCheckout.body).toMatchObject({ provider: "staging_sandbox", action: "staging_sandbox_checkout" });
  expect((await api(page, `/payments/staging-sandbox/${first.body.payment_attempt_id}/checkout`, "POST", { outcome: "DECLINE" })).status).toBe(202);
  await expect.poll(async () => (await api(page, "/subscriptions/mine")).body[0]?.status, { timeout: 15_000 }).toBe("payment_failed");

  const retry = await api(
    page,
    `/subscriptions/creator/${creatorSelf.body.id}`,
    "POST",
    { duration: "month_1" },
    { "Idempotency-Key": `staging-success-${stamp}` },
  );
  expect(retry.status, JSON.stringify(retry.body)).toBe(200);
  const checkout = await api(page, `/payments/${retry.body.payment_attempt_id}/checkout`);
  const payload = {
    id: `stg_e2e_subscription_success_${stamp}`,
    type: "payment.succeeded",
    payment_reference: checkout.body.provider_reference,
  };
  expect((await signedWebhook(request, payload)).status()).toBe(204);
  expect((await signedWebhook(request, payload)).status()).toBe(204);
  expect((await signedWebhook(request, { ...payload, id: `${payload.id}_invalid` }, "invalid")).status()).toBe(400);
  await expect.poll(async () => (await api(page, "/subscriptions/mine")).body[0]?.status, { timeout: 15_000 }).toBe("active");
});
