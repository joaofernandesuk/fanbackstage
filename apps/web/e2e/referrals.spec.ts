import { expect, test } from "@playwright/test";

import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };

async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown) {
  return page.evaluate(async ({ apiBase, path, method, body }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, {
      method, credentials: "include", headers: body ? {
        "Content-Type": "application/json",
        ...(path.includes("/checkout") ? { "Idempotency-Key": `e2e-referral-${Date.now()}` } : {}),
      } : undefined,
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
  const me = await api(page, "/me");
  expect(me.status).toBe(200);
  return me.body as { id: string };
}

async function followReferral(page: import("@playwright/test").Page, code: string) {
  await page.goto(`${apiBase}/api/v1/r/${code}`);
  await expect.poll(async () => (await page.context().cookies(apiBase)).some((cookie) => cookie.name === "fanbackstage_referral")).toBe(true);
}

async function createApprovedSeller(page: import("@playwright/test").Page, stamp: number, password: string) {
  const email = `phase10-creator-${stamp}@example.com`, username = `phase10creator${stamp}`;
  await register(page, email, password);
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Display name").fill("Phase 10 creator");
  await page.getByRole("button", { name: "Save profile" }).click();
  await page.getByRole("button", { name: "Submit application" }).click();
  await page.getByRole("button", { name: "Complete development verification" }).click();
  await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications");
  const application = applications.body.find((row: { username: string }) => row.username === username);
  expect(application).toBeTruthy();
  expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  await login(page, email, password);
  const listing = await api(page, "/marketplace/listings", "POST", {
    title: "Phase 10 referral item", category: "collectible", condition: "new", quantity_available: 3,
    price_amount_minor: 500, currency: "EUR", shipping_mode: "worldwide", origin_country_code: "PT",
    shipping_charged_minor: 100, media_asset_ids: [],
  });
  expect(listing.status).toBe(200);
  expect((await api(page, `/marketplace/listings/${listing.body.id}/submit`, "POST")).status).toBe(200);
  await login(page, admin.email, admin.password);
  expect((await api(page, `/marketplace/admin/listings/${listing.body.id}/moderation?approved=true`, "POST")).status).toBe(200);
  await api(page, "/admin/marketplace/shipping-allowances", "PUT", { country_code: "PT", currency: "EUR", allowed_shipping_minor: 100 });
  await api(page, "/admin/marketplace/hold-policies/new_seller", "PUT", { hold_duration_seconds: 0, active: true, is_default: true });
  return { email, listing: listing.body as { id: string; public_id: string; owner_creator_id: string } };
}

async function createProgram(page: import("@playwright/test").Page, payload: object, code: string) {
  const program = await api(page, "/admin/referrals/programs", "POST", payload);
  expect(program.status).toBe(200);
  const policy = await api(page, `/admin/referrals/programs/${program.body.id}/policies`, "POST", {
    basis_points: 1000, eligible_revenue_types: ["marketplace"], attribution_window_days: 30, subscription_reward_window_days: 90,
  });
  expect(policy.status).toBe(200);
  expect((await api(page, `/admin/referrals/programs/${program.body.id}/links`, "POST", {
    policy_id: policy.body.id, code, destination_path: "/", source: "playwright",
  })).status).toBe(200);
}

async function buyAndDeliver(page: import("@playwright/test").Page, listingPublicId: string, password: string, email: string) {
  await register(page, email, password);
  const order = await api(page, `/marketplace/listings/${listingPublicId}/checkout`, "POST", {
    quantity: 1, destination_country_code: "PT",
    shipping_address: { recipient_name: "Buyer", line1: "Private Street", city: "Lisbon", postal_code: "1000", country_code: "PT" },
  });
  expect(order.status, JSON.stringify(order.body)).toBe(200);
  expect((await api(page, `/payments/development/${order.body.payment_attempt_id}/complete`, "POST")).status).toBe(200);
  return order.body as { id: string; platform_fee_minor: number };
}

test("Phase 10 creator and affiliate referrals settle, release, reverse, and remain owner-scoped", async ({ page }) => {
  const stamp = Date.now(), password = "phase10-referral-password";
  const seller = await createApprovedSeller(page, stamp, password);

  await login(page, admin.email, admin.password);
  await createProgram(page, { actor_type: "creator", program_type: "creator_buyer_referral", owner_creator_id: seller.listing.owner_creator_id }, `CREATOR-${stamp}`);
  await followReferral(page, `CREATOR-${stamp}`);
  const creatorOrder = await buyAndDeliver(page, seller.listing.public_id, password, `phase10-creator-buyer-${stamp}@example.com`);
  await login(page, seller.email, password);
  expect((await api(page, `/marketplace/orders/${creatorOrder.id}/processing`, "POST")).body.status).toBe("processing");
  expect((await api(page, `/marketplace/orders/${creatorOrder.id}/shipped`, "POST", { carrier: "CTT", tracking_reference: "REF-CREATOR" })).body.status).toBe("shipped");
  await login(page, `phase10-creator-buyer-${stamp}@example.com`, password);
  expect((await api(page, `/marketplace/orders/${creatorOrder.id}/delivered`, "POST")).body.status).toBe("delivered");
  await login(page, admin.email, admin.password);
  expect((await api(page, "/admin/marketplace/earnings/release", "POST")).body.released).toBeGreaterThanOrEqual(1);
  await login(page, seller.email, password);
  const creatorDashboard = await api(page, "/r/me/dashboard");
  expect(creatorDashboard.body.totals_by_currency.EUR.available_amount_minor).toBe(Math.floor(creatorOrder.platform_fee_minor / 10));

  const affiliateAEmail = `phase10-affiliate-a-${stamp}@example.com`;
  const affiliateBEmail = `phase10-affiliate-b-${stamp}@example.com`;
  const affiliateA = await register(page, affiliateAEmail, password);
  const affiliateB = await register(page, affiliateBEmail, password);
  await login(page, admin.email, admin.password);
  const partner = await api(page, "/admin/affiliates", "POST", { name: "Phase 10 affiliate", owner_user_id: affiliateA.id });
  expect(partner.status).toBe(200);
  await createProgram(page, { actor_type: "affiliate_partner", program_type: "affiliate_referral", affiliate_partner_id: partner.body.id }, `AFFILIATE-${stamp}`);
  await followReferral(page, `AFFILIATE-${stamp}`);
  const affiliateOrder = await buyAndDeliver(page, seller.listing.public_id, password, `phase10-affiliate-buyer-${stamp}@example.com`);
  await login(page, affiliateAEmail, password);
  const pending = await api(page, "/r/me/dashboard");
  const reward = Math.floor(affiliateOrder.platform_fee_minor / 10);
  expect(pending.body.totals_by_currency.EUR.pending_amount_minor).toBe(reward);
  expect(pending.body.allocations).toHaveLength(1);
  await login(page, affiliateBEmail, password);
  const isolated = await api(page, "/r/me/dashboard");
  expect(isolated.body.allocations).toHaveLength(0);

  await login(page, seller.email, password);
  await api(page, `/marketplace/orders/${affiliateOrder.id}/processing`, "POST");
  await api(page, `/marketplace/orders/${affiliateOrder.id}/shipped`, "POST", { carrier: "CTT", tracking_reference: "REF-AFFILIATE" });
  await login(page, `phase10-affiliate-buyer-${stamp}@example.com`, password);
  await api(page, `/marketplace/orders/${affiliateOrder.id}/delivered`, "POST");
  await login(page, admin.email, admin.password);
  expect((await api(page, "/admin/marketplace/earnings/release", "POST")).body.released).toBeGreaterThanOrEqual(1);
  await login(page, affiliateAEmail, password);
  const available = await api(page, "/r/me/dashboard");
  expect(available.body.totals_by_currency.EUR.available_amount_minor).toBe(reward);

  await login(page, admin.email, admin.password);
  expect((await api(page, `/marketplace/admin/orders/${affiliateOrder.id}/refund`, "POST", { reason: "Phase 10 refund" })).body.status).toBe("refunded");
  await login(page, affiliateAEmail, password);
  const reversed = await api(page, "/r/me/dashboard");
  expect(reversed.body.totals_by_currency.EUR.reversed_amount_minor).toBe(reward);
  expect(reversed.body.totals_by_currency.EUR.available_amount_minor).toBe(0);

  // A chargeback follows the same immutable allocation, but before the
  // marketplace order becomes eligible for release.
  await followReferral(page, `AFFILIATE-${stamp}`);
  const chargebackOrder = await buyAndDeliver(
    page, seller.listing.public_id, password, `phase10-chargeback-buyer-${stamp}@example.com`
  );
  await login(page, admin.email, admin.password);
  expect((await api(page, `/marketplace/admin/orders/${chargebackOrder.id}/chargeback`, "POST", {
    reason: "Phase 10 chargeback",
  })).body.status).toBe("chargeback");
  await login(page, affiliateAEmail, password);
  const chargebackReversed = await api(page, "/r/me/dashboard");
  expect(chargebackReversed.body.totals_by_currency.EUR.reversed_amount_minor).toBe(reward * 2);
  expect(chargebackReversed.body.allocations).toHaveLength(2);
});
