import { expect, test } from "@playwright/test";

import { completeRegistrationCompliance, expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };
const manager = { email: "phase8-e2e-manager@example.com", password: "phase8-e2e-manager-password" };
async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown) { return page.evaluate(async ({ apiBase, path, method, body }) => { const headers = body ? { "Content-Type": "application/json", ...(path.includes("/checkout") ? { "Idempotency-Key": `e2e-${Date.now()}` } : {}) } : undefined; const response = await fetch(`${apiBase}/api/v1${path}`, { method, credentials: "include", headers, body: body ? JSON.stringify(body) : undefined }); return { status: response.status, body: await response.json().catch(() => null) }; }, { apiBase, path, method, body }); }
async function login(page: import("@playwright/test").Page, email: string, password: string) { await page.goto("/login"); await page.getByLabel("Email").fill(email); await page.getByLabel("Password", { exact: true }).fill(password); await page.getByRole("button", { name: "Log in" }).click(); await expectAuthenticatedAs(page, email); }
async function register(page: import("@playwright/test").Page, email: string, password: string) { await page.goto("/register"); await completeRegistrationCompliance(page); await page.getByLabel("Email").fill(email); await page.getByLabel("Password", { exact: true }).fill(password); await page.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check(); await page.getByRole("button", { name: "Create account" }).click(); await page.goto(await securityLink(email, "/verify-email")); await page.getByRole("button", { name: "Verify email" }).click(); await login(page, email, password); }

test("Phase 9 marketplace settles, fulfils, holds, and releases one immutable order", async ({ page }) => {
  const stamp = Date.now(), password = "phase9-marketplace-password", creatorEmail = `phase9-seller-${stamp}@example.com`, buyerEmail = `phase9-buyer-${stamp}@example.com`, username = `seller${stamp}`;
  await register(page, creatorEmail, password);
  await page.getByRole("link", { name: "Become a creator" }).click(); await page.getByLabel("Username").fill(username); await page.getByLabel("Display name").fill("Marketplace seller"); await page.getByRole("button", { name: "Save profile" }).click(); await page.getByRole("button", { name: "Submit application" }).click(); await page.getByRole("button", { name: "Complete development verification" }).click();
  await login(page, admin.email, admin.password); const applications = await api(page, "/admin/creator-applications"); const application = applications.body.find((row: { username: string }) => row.username === username); expect(application).toBeTruthy(); expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  await api(page, "/admin/marketplace/shipping-allowances", "PUT", { country_code: "PT", currency: "EUR", allowed_shipping_minor: 100 }); await api(page, "/admin/marketplace/hold-policies/new_seller", "PUT", { hold_duration_seconds: 0, active: true, is_default: true });
  await login(page, creatorEmail, password); await page.goto("/creator-onboarding"); await page.getByRole("checkbox", { name: "Make my approved creator profile public" }).check(); await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body, { timeout: 15_000 }).toMatchObject({ status: "approved", is_public: true });
  const listing = await api(page, "/marketplace/listings", "POST", { title: "Signed card", category: "collectible", condition: "new", quantity_available: 1, price_amount_minor: 500, currency: "EUR", shipping_mode: "worldwide", origin_country_code: "PT", shipping_charged_minor: 300, media_asset_ids: [] }); expect(listing.status).toBe(200);
  expect((await api(page, `/marketplace/listings/${listing.body.id}/submit`, "POST")).status).toBe(200); await login(page, admin.email, admin.password); expect((await api(page, `/marketplace/admin/listings/${listing.body.id}/moderation?approved=true`, "POST")).status).toBe(200);
  await register(page, buyerEmail, password);
  await page.goto(`/marketplace/${listing.body.public_id}`);
  await page.getByRole("button", { name: "Buy this item" }).click();
  await page.getByLabel("Recipient name").fill("Buyer");
  await page.getByLabel("Address line 1").fill("Private Street");
  await page.getByLabel("City").fill("Lisbon");
  await page.getByLabel("Postal code").fill("1000");
  await page.getByLabel("Country code").fill("PT");
  await page.getByRole("button", { name: "Review order" }).click();
  await expect(page.getByRole("heading", { name: "Review order" })).toBeVisible();
  await expect(page.getByText("Private Street")).toBeVisible();
  await page.getByRole("button", { name: "Place order and confirm payment" }).click();
  await expect(page.getByRole("heading", { name: "Order confirmed" })).toBeVisible();
  await expect(page.getByText("€8.00", { exact: true })).toBeVisible();
  const buyerOrders = await api(page, "/marketplace/orders/mine");
  const order = { status: buyerOrders.status, body: buyerOrders.body[0] };
  expect(order.status).toBe(200); expect(order.body.shipping_pass_through_minor).toBe(100); expect(order.body.shipping_excess_minor).toBe(200);
  await login(page, creatorEmail, password); expect((await api(page, `/marketplace/orders/${order.body.id}/processing`, "POST")).body.status).toBe("processing"); expect((await api(page, `/marketplace/orders/${order.body.id}/shipped`, "POST", { carrier: "CTT", tracking_reference: "PHASE9-TRACK" })).body.status).toBe("shipped");
  await login(page, buyerEmail, password); expect((await api(page, `/marketplace/orders/${order.body.id}/delivered`, "POST")).body.status).toBe("delivered"); const pending = await api(page, "/marketplace/orders/mine"); expect(pending.body[0].earnings_hold_until).toBeTruthy();
  await login(page, admin.email, admin.password); expect((await api(page, "/admin/marketplace/earnings/release", "POST")).body.released).toBe(1); expect((await api(page, "/admin/marketplace/earnings/release", "POST")).body.released).toBe(0);
});
