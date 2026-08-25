import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

import { mailpitContains, mailpitMessage, securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const harness = join(process.cwd(), "../api/tests/e2e_phase15_notification_harness.py");
const python = join(process.cwd(), "../api/.venv/bin/python");

type State = {
  intent_count: number;
  attempts: { status: string; provider_message_id: string | null; recipient: string | null }[];
  payload: Record<string, unknown> | null;
};

function releaseHarness<T>(...args: string[]): T {
  return JSON.parse(execFileSync(python, [harness, ...args], {
    env: { ...process.env, FANBACKSTAGE_E2E_RELEASE_VALIDATION: "1" },
    encoding: "utf8",
  })) as T;
}

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

async function registerAndLogin(page: Page, email: string, password: string) {
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.goto(await securityLink(email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByText(email)).toBeVisible();
}

async function providerEvent(provider_message_id: string, event: string, secret?: string) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(`${apiBase}/api/v1/notifications/provider-events`, {
        method: "POST", headers: { "Content-Type": "application/json", ...(secret ? { "X-FanBackstage-Provider-Secret": secret } : {}) },
        body: JSON.stringify({ provider_message_id, event }),
      });
      return response.status;
    } catch (error) {
      if (attempt === 2) throw error;
      await new Promise(resolve => setTimeout(resolve, 150));
    }
  }
  throw new Error("unreachable");
}

test("Phase 15 marketing race and provider replay use the isolated worker and Mailpit", async ({ browser, page }) => {
  const stamp = Date.now();
  const password = "phase15-release-password";
  const marketingEmail = `phase15-marketing-${stamp}@example.com`;
  await registerAndLogin(page, marketingEmail, password);
  expect((await api(page, "/notifications/preferences/marketing", "PUT", {
    email_enabled: true, in_app_enabled: true, consent: true,
  })).status).toBe(200);

  const marketing = releaseHarness<{ intent_id: string }>("queue", marketingEmail, "marketing", `unsubscribe-${stamp}`);
  const before = releaseHarness<State>("inspect", marketing.intent_id);
  expect(before.intent_count).toBe(1);
  expect(before.attempts).toEqual([]);
  const token = releaseHarness<{ token: string }>("unsubscribe-token", marketingEmail).token;
  expect((await api(page, `/notifications/unsubscribe-token?token=x${token.slice(1)}`, "POST")).status).toBe(400);
  expect((await api(page, `/notifications/unsubscribe-token?token=${encodeURIComponent(token)}`, "POST")).status).toBe(200);
  releaseHarness("enqueue", marketing.intent_id);
  await expect.poll(() => releaseHarness<State>("inspect", marketing.intent_id).attempts[0]?.status).toBe("suppressed");
  await expect.poll(() => mailpitContains(marketingEmail, `unsubscribe-${stamp}`), { timeout: 2_000 }).toBe(false);

  const ineligibleEmail = `phase15-ineligible-${stamp}@example.com`;
  const ineligibleContext = await browser.newContext();
  const ineligiblePage = await ineligibleContext.newPage();
  await registerAndLogin(ineligiblePage, ineligibleEmail, password);
  expect((await api(ineligiblePage, "/notifications/preferences/marketing", "PUT", {
    email_enabled: true, in_app_enabled: true, consent: true,
  })).status).toBe(200);
  const ineligible = releaseHarness<{ intent_id: string }>("queue", ineligibleEmail, "marketing", `ineligible-${stamp}`);
  releaseHarness("make-ineligible", ineligibleEmail);
  releaseHarness("enqueue", ineligible.intent_id);
  await expect.poll(() => releaseHarness<State>("inspect", ineligible.intent_id).attempts[0]?.status).toBe("suppressed");
  await expect.poll(() => mailpitContains(ineligibleEmail, `ineligible-${stamp}`), { timeout: 2_000 }).toBe(false);
  await ineligibleContext.close();

  const mandatory = releaseHarness<{ intent_id: string }>("queue", marketingEmail, "transactional", `mandatory-${stamp}`);
  releaseHarness("enqueue", mandatory.intent_id);
  await expect.poll(() => releaseHarness<State>("inspect", mandatory.intent_id).attempts[0]?.status).toBe("sent");
  const mandatoryMail = await mailpitMessage(marketingEmail, `mandatory-${stamp}`);
  expect(mandatoryMail.Subject).toBe(`Phase 15 transactional mandatory-${stamp}`);

  const webhookEmail = `phase15-webhook-${stamp}@example.com`;
  const webhookContext = await browser.newContext();
  const webhookPage = await webhookContext.newPage();
  await registerAndLogin(webhookPage, webhookEmail, password);
  const delivered = releaseHarness<{ intent_id: string }>("queue", webhookEmail, "transactional", `delivered-${stamp}`);
  releaseHarness("enqueue", delivered.intent_id);
  await expect.poll(() => releaseHarness<State>("inspect", delivered.intent_id).attempts[0]?.status).toBe("sent");
  const providerMessageId = releaseHarness<State>("inspect", delivered.intent_id).attempts[0]?.provider_message_id;
  expect(providerMessageId).toBeTruthy();
  expect(await providerEvent(providerMessageId!, "delivered")).toBe(401);
  const secret = process.env.FANBACKSTAGE_NOTIFICATION_WEBHOOK_SECRET;
  expect(secret, "isolated webhook secret is required").toBeTruthy();
  const webhookHeaders = { "X-FanBackstage-Provider-Secret": secret! };
  expect(await providerEvent(providerMessageId!, "accepted", webhookHeaders["X-FanBackstage-Provider-Secret"])).toBe(200);
  expect(await providerEvent(providerMessageId!, "delivered", webhookHeaders["X-FanBackstage-Provider-Secret"])).toBe(200);
  expect(releaseHarness<State>("inspect", delivered.intent_id).attempts[0].status).toBe("delivered");

  const bounced = releaseHarness<{ intent_id: string }>("queue", webhookEmail, "transactional", `bounce-${stamp}`);
  releaseHarness("enqueue", bounced.intent_id);
  await expect.poll(() => releaseHarness<State>("inspect", bounced.intent_id).attempts[0]?.status).toBe("sent");
  const bounceMessageId = releaseHarness<State>("inspect", bounced.intent_id).attempts[0].provider_message_id!;
  for (const event of ["deferred", "hard_bounce", "hard_bounce"]) {
    expect(await providerEvent(bounceMessageId, event, webhookHeaders["X-FanBackstage-Provider-Secret"])).toBe(200);
  }
  expect(releaseHarness<State>("inspect", bounced.intent_id).attempts[0].status).toBe("failed_permanent");
  expect(releaseHarness<{ count: number }>("suppression-count", webhookEmail).count).toBe(1);
  const suppressedFuture = releaseHarness<{ intent_id: string }>("queue", webhookEmail, "transactional", `suppressed-${stamp}`);
  releaseHarness("enqueue", suppressedFuture.intent_id);
  await expect.poll(() => releaseHarness<State>("inspect", suppressedFuture.intent_id).attempts[0]?.status).toBe("suppressed");
  await expect.poll(() => mailpitContains(webhookEmail, `suppressed-${stamp}`), { timeout: 2_000 }).toBe(false);
  await webhookContext.close();

  const complaintEmail = `phase15-complaint-${stamp}@example.com`;
  const complaintContext = await browser.newContext();
  const complaintPage = await complaintContext.newPage();
  await registerAndLogin(complaintPage, complaintEmail, password);
  const complaint = releaseHarness<{ intent_id: string }>("queue", complaintEmail, "transactional", `complaint-${stamp}`);
  releaseHarness("enqueue", complaint.intent_id);
  await expect.poll(() => releaseHarness<State>("inspect", complaint.intent_id).attempts[0]?.status).toBe("sent");
  const complaintMessageId = releaseHarness<State>("inspect", complaint.intent_id).attempts[0].provider_message_id!;
  expect(await providerEvent(complaintMessageId, "complaint", webhookHeaders["X-FanBackstage-Provider-Secret"])).toBe(200);
  expect(releaseHarness<{ count: number }>("suppression-count", complaintEmail).count).toBe(1);
  await complaintContext.close();
});
