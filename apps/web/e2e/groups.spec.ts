import { expect, test } from "@playwright/test";

import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const manager = { email: "phase8-e2e-manager@example.com", password: "phase8-e2e-manager-password" };
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };

async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown) {
  return page.evaluate(async ({ apiBase, path, method, body }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, { method, credentials: "include", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { apiBase, path, method, body });
}

async function login(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login"); await page.getByLabel("Email").fill(email); await page.getByLabel("Password").fill(password); await page.getByRole("button", { name: "Log in" }).click(); await expect(page.getByText(email)).toBeVisible();
}

async function register(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/register"); await page.getByLabel("Email").fill(email); await page.getByLabel("Password").fill(password); await page.getByRole("button", { name: "Create account" }).click(); await page.goto(await securityLink(email, "/verify-email")); await page.getByRole("button", { name: "Verify email" }).click(); await login(page, email, password);
}

test("Phase 8 manager proposal requires creator rejection or acceptance", async ({ page }) => {
  const stamp = Date.now(); const password = "phase8-contract-password"; const creatorEmail = `phase8-contract-${stamp}@example.com`; const username = `contract${stamp}`;
  await register(page, creatorEmail, password);
  await page.getByRole("link", { name: "Become a creator" }).click(); await page.getByLabel("Username").fill(username); await page.getByLabel("Display name").fill("Contract creator"); await page.getByRole("button", { name: "Save profile" }).click(); await page.getByRole("button", { name: "Submit application" }).click(); await page.getByRole("button", { name: "Complete development verification" }).click();
  await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications"); const application = applications.body.find((row: { username: string }) => row.username === username); expect(application).toBeTruthy(); expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  await login(page, manager.email, manager.password);
  const group = await api(page, "/groups", "POST", { name: `E2E contracts ${stamp}`, slug: `e2e-contracts-${stamp}`, default_creator_basis_points: 5000 }); expect(group.status).toBe(200);
  const invitation = await api(page, `/groups/${group.body.id}/invitations`, "POST", { creator_id: application.id, creator_basis_points: 5000, permissions: [] }); expect(invitation.status).toBe(200);
  await login(page, creatorEmail, password); await page.goto("/creator-studio"); await page.getByRole("button", { name: "Accept", exact: true }).click(); await expect(page.getByText("v1: 50% / 50% (active)")).toBeVisible();
  await login(page, manager.email, manager.password); await page.goto("/groups"); await page.getByLabel("Group").selectOption(group.body.id); await expect(page.getByLabel("Group")).toHaveValue(group.body.id); await expect(page.locator("li", { hasText: "Contract creator — active" })).toBeVisible();
  const split = page.getByLabel("Proposed creator split for Contract creator"); await split.fill("7000"); await page.getByRole("button", { name: "Propose amendment" }).click(); await expect(page.getByRole("status")).toContainText("creator must explicitly accept");
  await login(page, creatorEmail, password); await page.goto("/creator-studio"); await page.locator("li", { hasText: group.body.id }).getByRole("button", { name: "Reject amendment" }).click(); await expect(page.locator("li", { hasText: group.body.id }).getByText("rejected")).toBeVisible();
  await login(page, manager.email, manager.password); expect((await api(page, `/groups/memberships/${invitation.body.id}/amendments`, "POST", { creator_basis_points: 7000 })).status).toBe(200);
  await login(page, creatorEmail, password); await page.goto("/creator-studio"); await page.locator("li", { hasText: group.body.id }).getByRole("button", { name: "Accept amendment" }).click();
  const memberships = await api(page, "/groups/mine/memberships"); const membership = memberships.body.find((row: { group_id: string }) => row.group_id === group.body.id); expect(membership).toBeTruthy(); const contracts = [...membership.contracts].sort((left: { version: number }, right: { version: number }) => left.version - right.version); expect(contracts.map((contract: { status: string }) => contract.status)).toEqual(["ended", "rejected", "active"]); expect(contracts[2].creator_basis_points).toBe(7000);
});
