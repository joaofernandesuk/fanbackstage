import { expect, test } from "@playwright/test";

import { completeRegistrationCompliance, expectAuthenticatedAs } from "./auth-helpers";
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
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);
}

async function register(page: import("@playwright/test").Page, email: string, password: string) {
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
  expect((await api(page, "/featuring/admin/prices", "POST", { slot_id: slot.body.id, target_type: "creator", duration_seconds: 6, amount_minor: 900, currency: "EUR" })).status).toBe(200);
  await login(page, email, password);
  await page.goto("/creator-onboarding");
  await page.getByRole("checkbox", { name: "Make my approved creator profile public" }).check();
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body, { timeout: 15_000 }).toMatchObject({ status: "approved", is_public: true });
  const targets = await api(page, "/featuring/eligible-targets");
  const target = targets.body.find((row: { target_id: string; target_type: string }) => row.target_type === "creator" && row.target_id === application.id);
  expect(target, JSON.stringify(targets.body)).toBeTruthy();
  const booking = await api(page, "/featuring/bookings", "POST", { slot_id: slot.body.id, target_type: "creator", target_id: target.target_id, starts_at: new Date(Date.now() + 2500).toISOString(), duration_seconds: 6 });
  expect(booking.status, JSON.stringify(booking.body)).toBe(200);
  await page.goto("/featuring");
  await page.getByRole("button", { name: "Review and authorize payment" }).click();
  await expect(page.getByRole("heading", { name: "Review featuring payment" })).toBeVisible();
  await expect(page.getByText("A failed attempt does not count as payment.")).toBeVisible();
  await page.getByRole("button", { name: /Confirm test payment/ }).click();
  await expect(page.getByRole("status")).toContainText("Test payment confirmed");
  await login(page, admin.email, admin.password);
  await expect.poll(async () => (await api(page, "/featuring/admin/reconcile", "POST")).body.activated, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
  await page.goto("/discover");
  await expect(page.getByLabel("Sponsored placement")).toBeVisible();
  await expect(page.locator("article", { hasText: username })).toHaveCount(1);
  await expect.poll(async () => {
    await api(page, "/featuring/admin/reconcile", "POST");
    const bookings = await api(page, "/featuring/admin/bookings");
    return bookings.body.find((row: { id: string }) => row.id === booking.body.id)?.status;
  }, { timeout: 15_000 }).toBe("completed");
  await page.reload();
  await expect(page.getByLabel("Sponsored placement")).toHaveCount(0);
});
