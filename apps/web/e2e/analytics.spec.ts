import { expect, test, type Page } from "@playwright/test";

import { expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };
const manager = { email: "phase8-e2e-manager@example.com", password: "phase8-e2e-manager-password" };

type Result = { status: number; body: any };

async function api(page: Page, path: string, method = "GET", body?: unknown, key?: string): Promise<Result> {
  return page.evaluate(async ({ apiBase, path, method, body, key }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, {
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}) } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { apiBase, path, method, body, key });
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
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.goto(await securityLink(email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await login(page, email, password);
  return (await api(page, "/me")).body as { id: string };
}

async function approvedCreator(page: Page, stamp: number, label: string) {
  const password = "phase14-analytics-password";
  const email = `phase14-${label}-${stamp}@example.com`;
  const username = `analytics${label}${stamp}`;
  await register(page, email, password);
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Display name").fill(`Analytics ${label}`);
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body.username, { timeout: 15_000 }).toBe(username);
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body.status, { timeout: 15_000 }).toBe("pending_verification");
  await page.getByRole("button", { name: "Complete development verification" }).click();
  await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications");
  const application = applications.body.find((item: { username: string }) => item.username === username);
  expect(application).toBeTruthy();
  expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  await login(page, email, password);
  await page.goto("/creator-onboarding");
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body, { timeout: 15_000 }).toMatchObject({ status: "approved", is_public: true });
  return { email, password, username, creatorId: application.id as string };
}

async function listing(page: Page, title: string, currency: string, amount: number) {
  const row = await api(page, "/marketplace/listings", "POST", {
    title, category: "collectible", condition: "new", quantity_available: 10,
    price_amount_minor: amount, currency, shipping_mode: "worldwide", origin_country_code: "PT",
    shipping_charged_minor: 0, media_asset_ids: [],
  });
  expect(row.status, JSON.stringify(row.body)).toBe(200);
  expect((await api(page, `/marketplace/listings/${row.body.id}/submit`, "POST")).status).toBe(200);
  await login(page, admin.email, admin.password);
  expect((await api(page, `/marketplace/admin/listings/${row.body.id}/moderation?approved=true`, "POST")).status).toBe(200);
  return row.body as { id: string; public_id: string };
}

async function paidOrder(page: Page, publicId: string, stamp: number) {
  const row = await api(page, `/marketplace/listings/${publicId}/checkout`, "POST", {
    quantity: 1, destination_country_code: "PT",
    shipping_address: { recipient_name: "Buyer", line1: "Private Street", city: "Lisbon", postal_code: "1000", country_code: "PT" },
  }, `phase14-order-${stamp}-${publicId}`);
  expect(row.status, JSON.stringify(row.body)).toBe(200);
  expect((await api(page, `/payments/development/${row.body.payment_attempt_id}/complete`, "POST")).status).toBe(200);
  return row.body as { id: string; total_paid_minor: number; platform_fee_minor: number; creator_amount_minor: number; group_amount_minor: number; currency: string };
}

function currency(report: any, code: string) {
  const row = report.currencies.find((item: { currency: string }) => item.currency === code);
  expect(row, JSON.stringify(report)).toBeTruthy();
  return row;
}

test("Phase 14 creator analytics is ledger-derived, currency-separated, and owner-scoped", async ({ browser, page }) => {
  const stamp = Date.now();
  const creator = await approvedCreator(page, stamp, "creator-a");
  await login(page, admin.email, admin.password);
  await api(page, "/admin/marketplace/shipping-allowances", "PUT", { country_code: "PT", currency: "EUR", allowed_shipping_minor: 0 });
  await api(page, "/admin/marketplace/shipping-allowances", "PUT", { country_code: "PT", currency: "USD", allowed_shipping_minor: 0 });
  await login(page, creator.email, creator.password);
  expect((await api(page, "/creator/subscription-plan", "PUT", { currency: "EUR", enabled: true, prices: [{ duration: "month_1", amount_minor: 1000, enabled: true }] })).status).toBe(200);
  const eur = await listing(page, `Analytics EUR ${stamp}`, "EUR", 1000);
  await login(page, creator.email, creator.password);
  const usd = await listing(page, `Analytics USD ${stamp}`, "USD", 1000);

  const buyerContext = await browser.newContext(); const buyer = await buyerContext.newPage();
  await register(buyer, `phase14-creator-buyer-${stamp}@example.com`, creator.password);
  const refunded = await paidOrder(buyer, eur.public_id, stamp);
  const usdOrder = await paidOrder(buyer, usd.public_id, stamp + 1);
  await buyer.goto(`/creator/${creator.username}`);
  await buyer.locator('section[aria-label="Subscriptions"]').getByRole("button", { name: "Subscribe" }).click();
  await expect(buyer.getByText("Subscription is active.")).toBeVisible();
  await login(page, admin.email, admin.password);
  expect((await api(page, `/marketplace/admin/orders/${refunded.id}/refund`, "POST", { reason: "Phase 14 refund" })).status).toBe(200);

  await login(page, creator.email, creator.password);
  const report = await api(page, "/analytics/creator/overview");
  expect(report.status).toBe(200);
  const eurTotals = currency(report.body, "EUR"); const usdTotals = currency(report.body, "USD");
  expect(eurTotals.gross_sales_minor).toBeGreaterThan(refunded.creator_amount_minor);
  expect(eurTotals.reversed_minor).toBe(refunded.creator_amount_minor);
  expect(eurTotals.creator_net_minor).toBeGreaterThan(0); // subscription remains after the refunded order
  expect(usdTotals.gross_sales_minor).toBe(usdOrder.creator_amount_minor);
  expect(usdTotals.creator_net_minor).toBe(usdOrder.creator_amount_minor);
  await page.goto("/creator-studio/analytics");
  await expect(page.getByRole("heading", { name: "Analytics" })).toBeVisible();
  await expect(page.getByText("Privacy-preserving commercial and operational analytics.")).toBeVisible();

  const creatorB = await approvedCreator(page, stamp, "creator-b");
  await login(page, creatorB.email, creatorB.password);
  const isolated = await api(page, "/analytics/creator/overview");
  expect(isolated.status).toBe(200);
  expect(isolated.body.creator_id).toBe(creatorB.creatorId);
  expect(isolated.body.currencies).toEqual([]);
  await buyerContext.close();
});

test("Phase 14 group analytics keeps historical allocations after contract change and departure", async ({ browser, page }) => {
  const stamp = Date.now(); const creator = await approvedCreator(page, stamp, "group");
  await login(page, manager.email, manager.password);
  const group = await api(page, "/groups", "POST", { name: `Analytics group ${stamp}`, slug: `analytics-group-${stamp}`, default_creator_basis_points: 5000 });
  expect(group.status).toBe(200);
  const membership = await api(page, `/groups/${group.body.id}/invitations`, "POST", { creator_id: creator.creatorId, creator_basis_points: 5000, permissions: ["view_analytics"] });
  expect(membership.status).toBe(200);
  await login(page, creator.email, creator.password);
  expect((await api(page, `/groups/memberships/${membership.body.id}/accept`, "POST")).status).toBe(200);
  await login(page, admin.email, admin.password);
  await api(page, "/admin/marketplace/shipping-allowances", "PUT", { country_code: "PT", currency: "EUR", allowed_shipping_minor: 0 });
  await login(page, creator.email, creator.password);
  const item = await listing(page, `Group allocation ${stamp}`, "EUR", 1000);
  const buyerContext = await browser.newContext(); const buyer = await buyerContext.newPage();
  await register(buyer, `phase14-group-buyer-${stamp}@example.com`, creator.password);
  const order = await paidOrder(buyer, item.public_id, stamp);
  await login(page, manager.email, manager.password);
  const before = await api(page, `/analytics/groups/${group.body.id}/overview`);
  expect(before.status).toBe(200);
  expect(currency(before.body, "EUR").group_net_minor).toBe(order.group_amount_minor);
  await page.goto("/groups/analytics"); await page.getByLabel("Group ID").fill(group.body.id); await page.getByRole("button", { name: "Load analytics" }).click();
  await expect(page.getByText("Group KPIs")).toBeVisible();
  const amendment = await api(page, `/groups/memberships/${membership.body.id}/amendments`, "POST", { creator_basis_points: 8000 });
  await login(page, creator.email, creator.password);
  expect((await api(page, `/groups/contracts/${amendment.body.id}/accept`, "POST")).status).toBe(200);
  await login(page, manager.email, manager.password);
  const afterContract = await api(page, `/analytics/groups/${group.body.id}/overview`);
  expect(currency(afterContract.body, "EUR").group_net_minor).toBe(order.group_amount_minor);
  expect((await api(page, `/groups/memberships/${membership.body.id}`, "DELETE")).status).toBe(200);
  const historical = await api(page, `/analytics/groups/${group.body.id}/overview`);
  expect(currency(historical.body, "EUR").group_net_minor).toBe(order.group_amount_minor);
  expect((await api(page, `/analytics/groups/${group.body.id}/creators`)).body.active_managed_creator_ids).toEqual([]);
  expect((await api(page, `/groups/managed-creators/${creator.creatorId}/analytics`)).status).toBe(403);
  await buyerContext.close();
});

test("Phase 14 admin BI and attribution dimensions coexist without mutation", async ({ browser, page }) => {
  const stamp = Date.now(); const creator = await approvedCreator(page, stamp, "bi");
  await login(page, admin.email, admin.password);
  const deniedContext = await browser.newContext(); const denied = await deniedContext.newPage();
  await register(denied, `phase14-denied-${stamp}@example.com`, creator.password);
  expect((await api(denied, "/analytics/platform/overview")).status).toBe(403);
  await deniedContext.close();
  await api(page, "/admin/marketplace/shipping-allowances", "PUT", { country_code: "PT", currency: "EUR", allowed_shipping_minor: 0 });
  const program = await api(page, "/admin/referrals/programs", "POST", { actor_type: "creator", program_type: "creator_buyer_referral", owner_creator_id: creator.creatorId });
  const policy = await api(page, `/admin/referrals/programs/${program.body.id}/policies`, "POST", { basis_points: 1000, eligible_revenue_types: ["marketplace"], attribution_window_days: 30, subscription_reward_window_days: 90 });
  const code = `ANALYTICS-${stamp}`;
  expect((await api(page, `/admin/referrals/programs/${program.body.id}/links`, "POST", { policy_id: policy.body.id, code, destination_path: "/", source: "playwright" })).status).toBe(200);
  await login(page, creator.email, creator.password);
  const item = await listing(page, `BI allocation ${stamp}`, "EUR", 1000);
  await login(page, admin.email, admin.password);
  const surface = await api(page, "/featuring/admin/surfaces", "POST", { kind: "discover_home_hero", cancellation_cutoff_seconds: 3600 });
  const slot = await api(page, "/featuring/admin/slots", "POST", { surface_id: surface.body.id, slot_key: `analytics-${stamp}`, position: 0, capacity: 1 });
  expect((await api(page, "/featuring/admin/prices", "POST", { slot_id: slot.body.id, target_type: "creator", duration_seconds: 2, amount_minor: 300, currency: "EUR" })).status).toBe(200);
  const buyerContext = await browser.newContext(); const buyer = await buyerContext.newPage();
  await buyer.goto(`${apiBase}/api/v1/r/${code}`);
  await expect.poll(async () => (await buyer.context().cookies(apiBase)).some((cookie) => cookie.name === "fanbackstage_referral")).toBe(true);
  await register(buyer, `phase14-attributed-${stamp}@example.com`, creator.password);
  expect((await api(buyer, `/discovery/events/click?entity_type=creator&entity_id=${creator.creatorId}`, "POST")).status).toBe(200);
  const order = await paidOrder(buyer, item.public_id, stamp);
  await login(page, creator.email, creator.password);
  const targets = await api(page, "/featuring/eligible-targets");
  const booking = await api(page, "/featuring/bookings", "POST", { slot_id: slot.body.id, target_type: "creator", target_id: targets.body[0].target_id, starts_at: new Date(Date.now() + 1000).toISOString(), duration_seconds: 2 }, `phase14-feature-${stamp}`);
  expect(booking.status, JSON.stringify(booking.body)).toBe(200);
  const payment = await api(page, `/featuring/bookings/${booking.body.id}/payment`, "POST");
  expect((await api(page, `/payments/development/${payment.body.payment_attempt_id}/complete`, "POST")).status).toBe(200);
  await login(page, admin.email, admin.password);
  await expect.poll(async () => (await api(page, "/featuring/admin/reconcile", "POST")).body.activated, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
  await login(buyer, `phase14-attributed-${stamp}@example.com`, creator.password);
  expect((await api(buyer, `/discovery/events/sponsored/${booking.body.id}/sponsored_click`, "POST")).status).toBe(200);
  expect((await api(buyer, `/discovery/events/sponsored/${booking.body.id}/sponsored_conversion`, "POST")).status).toBe(200);
  await login(page, admin.email, admin.password);
  const overview = await api(page, "/analytics/platform/overview"); const eur = currency(overview.body, "EUR");
  expect(eur.gmv_minor).toBeGreaterThanOrEqual(order.total_paid_minor + 300);
  expect(eur.platform_fee_minor).toBeGreaterThanOrEqual(order.platform_fee_minor + 300);
  expect(eur.creator_distributable_minor).toBeGreaterThanOrEqual(order.creator_amount_minor);
  expect(eur.platform_retained_net_minor).toBe(eur.platform_fee_minor - eur.referral_affiliate_commission_minor);
  expect(eur.featuring_revenue_minor).toBeGreaterThanOrEqual(300);
  const growth = await api(page, "/analytics/platform/growth");
  expect(growth.body.attribution_dimensions).toMatchObject({ referral_acquisition: expect.any(Number), organic_discovery_interactions: expect.any(Number), sponsored_featuring_interactions: expect.any(Number), financial_allocations: expect.any(Number) });
  expect(growth.body.attribution_dimensions.organic_discovery_interactions).toBeGreaterThanOrEqual(1);
  expect(growth.body.attribution_dimensions.sponsored_featuring_interactions).toBeGreaterThanOrEqual(2);
  expect(growth.body.attribution_dimensions.financial_allocations).toBeGreaterThanOrEqual(1);
  await expect.poll(async () => (await api(page, "/featuring/admin/reconcile", "POST")).body.deactivated, { timeout: 15_000 }).toBeGreaterThanOrEqual(1);
  await page.goto("/admin/analytics"); await expect(page.getByRole("heading", { name: "Platform BI" })).toBeVisible();
  await buyerContext.close();
});
