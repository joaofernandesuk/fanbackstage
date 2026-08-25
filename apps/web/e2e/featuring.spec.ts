import { expect, test } from "@playwright/test";

import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };

async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown) {
  return page.evaluate(async ({ apiBase, path, method, body }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, {
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json", ...(path === "/featuring/bookings" ? { "Idempotency-Key": `feature-${Date.now()}` } : {}) } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { apiBase, path, method, body });
}

async function login(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByText(email)).toBeVisible();
}

async function register(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.goto(await securityLink(email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await login(page, email, password);
}

test("Phase 12 booking settles, inserts one labelled sponsored card, and expires", async ({ page }) => {
  const stamp = Date.now();
  const password = "phase12-feature-password";
  const email = `phase12-feature-${stamp}@example.com`;
  const username = `feature${stamp}`;
  await register(page, email, password);
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Display name").fill("Featured Creator");
  await page.getByRole("button", { name: "Save profile" }).click();
  await page.getByRole("button", { name: "Submit application" }).click();
  await page.getByRole("button", { name: "Complete development verification" }).click();
  await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications");
  const application = applications.body.find((row: { username: string }) => row.username === username);
  expect(application).toBeTruthy();
  expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  const surface = await api(page, "/featuring/admin/surfaces", "POST", { kind: "discover_home_hero", cancellation_cutoff_seconds: 3600 });
  const slot = await api(page, "/featuring/admin/slots", "POST", { surface_id: surface.body.id, slot_key: `hero-${stamp}`, position: 0, capacity: 1 });
  expect((await api(page, "/featuring/admin/prices", "POST", { slot_id: slot.body.id, target_type: "creator", duration_seconds: 2, amount_minor: 900, currency: "EUR" })).status).toBe(200);
  await login(page, email, password);
  await page.goto("/creator-onboarding");
  await page.getByRole("button", { name: "Save profile" }).click();
  const targets = await api(page, "/featuring/eligible-targets");
  const booking = await api(page, "/featuring/bookings", "POST", { slot_id: slot.body.id, target_type: "creator", target_id: targets.body[0].target_id, starts_at: new Date(Date.now() + 1100).toISOString(), duration_seconds: 2 });
  expect(booking.status, JSON.stringify(booking.body)).toBe(200);
  const payment = await api(page, `/featuring/bookings/${booking.body.id}/payment`, "POST");
  expect((await api(page, `/payments/development/${payment.body.payment_attempt_id}/complete`, "POST")).status).toBe(200);
  await login(page, admin.email, admin.password);
  await expect.poll(async () => (await api(page, "/featuring/admin/reconcile", "POST")).body.activated, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
  await page.goto("/discover");
  await expect(page.getByLabel("Sponsored placement")).toBeVisible();
  await expect(page.locator("article", { hasText: username })).toHaveCount(1);
  await expect.poll(async () => (await api(page, "/featuring/admin/reconcile", "POST")).body.deactivated, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
  await page.reload();
  await expect(page.getByLabel("Sponsored placement")).toHaveCount(0);
});
