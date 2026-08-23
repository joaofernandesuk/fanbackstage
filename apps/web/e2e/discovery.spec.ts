import { expect, test } from "@playwright/test";

import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };
async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown) { return page.evaluate(async ({ apiBase, path, method, body }) => { const response = await fetch(`${apiBase}/api/v1${path}`, { method, credentials: "include", headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined }); return { status: response.status, body: await response.json().catch(() => null) }; }, { apiBase, path, method, body }); }
async function login(page: import("@playwright/test").Page, email: string, password: string) { await page.goto("/login"); await page.getByLabel("Email").fill(email); await page.getByLabel("Password").fill(password); await page.getByRole("button", { name: "Log in" }).click(); await expect(page.getByText(email)).toBeVisible(); }
async function register(page: import("@playwright/test").Page, email: string, password: string) { await page.goto("/register"); await page.getByLabel("Email").fill(email); await page.getByLabel("Password").fill(password); await page.getByRole("button", { name: "Create account" }).click(); await page.goto(await securityLink(email, "/verify-email")); await page.getByRole("button", { name: "Verify email" }).click(); await login(page, email, password); }

test("Phase 11 discovery uses safe public projections, blocks, and versioned organic controls", async ({ browser, page }) => {
  const stamp = Date.now(), password = "phase11-discovery-password", creatorEmail = `phase11-creator-${stamp}@example.com`, username = `discover${stamp}`;
  await register(page, creatorEmail, password); await page.getByRole("link", { name: "Become a creator" }).click(); await page.getByLabel("Username").fill(username); await page.getByLabel("Display name").fill("Discovery Creator"); await page.getByRole("button", { name: "Save profile" }).click(); await page.getByRole("button", { name: "Submit application" }).click(); await page.getByRole("button", { name: "Complete development verification" }).click();
  await login(page, admin.email, admin.password); const applications = await api(page, "/admin/creator-applications"); const application = applications.body.find((row: { username: string }) => row.username === username); expect(application).toBeTruthy(); expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200); await login(page, creatorEmail, password); await page.goto("/creator-onboarding"); await page.getByRole("button", { name: "Save profile" }).click();
  await login(page, admin.email, admin.password); const config = await api(page, "/discovery/admin/config"); expect(config.status).toBe(200); expect((await api(page, "/discovery/admin/config", "PUT", { ...config.body, text_weight: 99 })).body.version).toBe(config.body.version + 1);
  const viewerContext = await browser.newContext(); const viewer = await viewerContext.newPage(); await register(viewer, `phase11-viewer-${stamp}@example.com`, password);
  const anonymous = await api(viewer, `/discovery/search?q=discover${stamp}&types=creator`); expect(anonymous.status).toBe(200); expect(anonymous.body.items).toHaveLength(1); expect(anonymous.body.items[0]).toMatchObject({ entity_type: "creator", creator_username: username });
  await login(page, admin.email, admin.password); expect((await api(page, "/discovery/admin/hides", "POST", { entity_type: "creator", entity_id: application.id, reason: "e2e operational hide" })).status).toBe(200); expect((await api(viewer, `/discovery/search?q=discover${stamp}&types=creator`)).body.items).toHaveLength(0);
  await viewerContext.close();
});
