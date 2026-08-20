import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { securityLink } from "./mailpit";

const apiBase =
  process.env.E2E_API_URL ?? process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://127.0.0.1:8000";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };
const image = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

function videoFixture(): Buffer {
  const directory = mkdtempSync(join(tmpdir(), "fanbackstage-phase2-"));
  const path = join(directory, "video.mp4");
  execFileSync("ffmpeg", ["-y", "-f", "lavfi", "-i", "color=c=purple:s=32x24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", path], { stdio: "ignore" });
  return readFileSync(path);
}

async function login(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByText(email)).toBeVisible();
}

async function approve(page: import("@playwright/test").Page, path: string) {
  const response = await page.evaluate(async ({ apiBase, path }) => {
    const result = await fetch(`${apiBase}/api/v1${path}`, { method: "POST", credentials: "include" });
    return { status: result.status, body: await result.json() };
  }, { apiBase, path });
  expect(response.status).toBe(200);
}

test("creator media travels through the real private processing stack", async ({ browser, page }) => {
  const stamp = Date.now();
  const email = `phase2-media-${stamp}@example.com`;
  const password = "phase2-media-password";
  const username = `media${stamp}`;
  const galleryTitle = `Private gallery ${stamp}`;

  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.goto(await securityLink(email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await login(page, email, password);
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Display name").fill("Media E2E Creator");
  await page.getByRole("button", { name: "Save profile" }).click();
  await page.getByRole("button", { name: "Submit application" }).click();
  await page.getByRole("button", { name: "Complete development verification" }).click();
  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();

  await login(page, admin.email, admin.password);
  const applications = await page.evaluate(async ({ apiBase }) => {
    const result = await fetch(`${apiBase}/api/v1/admin/creator-applications`, { credentials: "include" });
    return { status: result.status, body: await result.json() };
  }, { apiBase });
  expect(applications.status).toBe(200);
  const application = applications.body.find((item: { username: string }) => item.username === username);
  expect(application).toBeTruthy();
  await approve(page, `/admin/creator-applications/${application.id}/approve`);
  await page.getByRole("button", { name: "Log out" }).click();

  await login(page, email, password);
  await page.goto("/creator-onboarding");
  await page.getByRole("button", { name: "Save profile" }).click();
  await page.goto("/account");
  await page.getByRole("link", { name: "Creator studio" }).click();
  const browserErrors: string[] = [];
  const storageRequests: { url: string; method: string; headers: Record<string, string> }[] = [];
  const storageResponses: { url: string; status: number; headers: Record<string, string>; body: string }[] = [];
  const storageFailures: { url: string; error: string | null }[] = [];
  page.on("console", message => { if (message.type() === "error") browserErrors.push(message.text()); });
  page.on("request", request => {
    if (request.method() === "PUT" && request.url().includes("X-Amz-Algorithm")) {
      storageRequests.push({ url: request.url(), method: request.method(), headers: request.headers() });
    }
  });
  page.on("response", async response => {
    if (response.request().method() === "PUT" && response.url().includes("X-Amz-Algorithm")) {
      storageResponses.push({
        url: response.url(),
        status: response.status(),
        headers: response.headers(),
        body: await response.text().catch(() => ""),
      });
    }
  });
  page.on("requestfailed", request => {
    if (request.method() === "PUT" && request.url().includes("X-Amz-Algorithm")) {
      storageFailures.push({ url: request.url(), error: request.failure()?.errorText ?? null });
    }
  });
  await page.getByLabel("Upload image or video").setInputFiles({ name: "gallery.png", mimeType: "image/png", buffer: image });
  await page.getByRole("button", { name: "Upload media" }).click();
  await expect.poll(() => storageRequests.length, { timeout: 10000 }).toBe(1);
  await expect.poll(() => storageResponses.length + storageFailures.length, { timeout: 10000 }).toBeGreaterThan(0);
  if (!storageResponses.some(response => response.status === 200)) {
    throw new Error(JSON.stringify({ browserErrors, storageRequests, storageResponses, storageFailures }));
  }
  await test.info().attach("direct-upload-diagnostics.json", {
    body: JSON.stringify({ browserErrors, storageRequests, storageResponses, storageFailures }, null, 2),
    contentType: "application/json",
  });
  await expect.poll(async () => page.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/media/mine`, { credentials: "include" });
    return (await response.json() as { status: string }[]).filter(asset => asset.status === "ready").length;
  }, { apiBase }), { timeout: 30000 }).toBeGreaterThan(0);
  await page.reload();
  await page.getByRole("group", { name: "Ready images" }).getByRole("checkbox").check();
  await page.getByLabel("Gallery title").fill(galleryTitle);
  await page.getByLabel("Access policy").first().selectOption("ppv");
  await page.getByLabel("PPV price (minor units; only when PPV)").first().fill("999");
  await page.getByRole("button", { name: "Create and submit gallery" }).click();
  await expect(page.getByText(/pending_review/)).toBeVisible();

  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();
  await login(page, admin.email, admin.password);
  const content = await page.evaluate(async ({ apiBase, galleryTitle }) => {
    const result = await fetch(`${apiBase}/api/v1/admin/content-review`, { credentials: "include" });
    return (await result.json()).find((item: { title: string }) => item.title === galleryTitle);
  }, { apiBase, galleryTitle });
  await approve(page, `/admin/content-review/${content.id}/approve`);
  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();

  await login(page, email, password);
  const owner = await page.evaluate(async ({ apiBase, contentId }) => {
    const result = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, { credentials: "include" });
    return await result.json();
  }, { apiBase, contentId: content.id });
  expect(owner.has_access).toBe(true);
  expect(JSON.stringify(owner)).not.toContain("original/");
  await page.goto("/creator-studio");
  await page.getByLabel("Enable subscriptions").check();
  for (const [index, price] of ["1000", "3000", "6000", "12000"].entries()) {
    await page.getByLabel("Price (minor units)").nth(index).fill(price);
  }
  await page.getByRole("button", { name: "Save subscription plan" }).click();
  await expect(page.getByText("Subscription plan saved.")).toBeVisible();
  const buyerContext = await browser.newContext();
  const buyerPage = await buyerContext.newPage();
  const buyerEmail = `phase3-buyer-${stamp}@example.com`;
  await buyerPage.goto("/register");
  await buyerPage.getByLabel("Email").fill(buyerEmail);
  await buyerPage.getByLabel("Password").fill(password);
  await buyerPage.getByRole("button", { name: "Create account" }).click();
  await buyerPage.goto(await securityLink(buyerEmail, "/verify-email"));
  await buyerPage.getByRole("button", { name: "Verify email" }).click();
  await login(buyerPage, buyerEmail, password);
  await buyerPage.goto(`/creator/${username}`);
  await buyerPage.getByRole("button", { name: "Unlock for 999 EUR" }).click();
  await expect(buyerPage.getByText("LOCKED")).toHaveCount(0);
  const buyerContent = await buyerPage.evaluate(async ({ apiBase, contentId }) => {
    const response = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, { credentials: "include" });
    return await response.json();
  }, { apiBase, contentId: content.id });
  expect(buyerContent.has_access).toBe(true);
  await buyerContext.close();
  const subscriberContext = await browser.newContext();
  const subscriberPage = await subscriberContext.newPage();
  const subscriberEmail = `phase4-subscriber-${stamp}@example.com`;
  await subscriberPage.goto("/register");
  await subscriberPage.getByLabel("Email").fill(subscriberEmail);
  await subscriberPage.getByLabel("Password").fill(password);
  await subscriberPage.getByRole("button", { name: "Create account" }).click();
  await subscriberPage.goto(await securityLink(subscriberEmail, "/verify-email"));
  await subscriberPage.getByRole("button", { name: "Verify email" }).click();
  await login(subscriberPage, subscriberEmail, password);
  await subscriberPage.goto(`/creator/${username}`);
  await subscriberPage.getByRole("button", { name: "Subscribe" }).first().click();
  await expect(subscriberPage.getByText("Subscription is active.")).toBeVisible();
  const subscription = await subscriberPage.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/subscriptions/mine`, { credentials: "include" });
    return { status: response.status, body: await response.json() };
  }, { apiBase });
  expect(subscription.status).toBe(200);
  expect(subscription.body).toHaveLength(1);
  expect(subscription.body[0]).toMatchObject({ duration: "month_1", status: "active", auto_renew: true });
  const cancel = await subscriberPage.evaluate(async ({ apiBase, id }) => {
    const response = await fetch(`${apiBase}/api/v1/subscriptions/${id}/auto-renew`, { method: "PATCH", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: false }) });
    return { status: response.status, body: await response.json() };
  }, { apiBase, id: subscription.body[0].id });
  expect(cancel.body).toMatchObject({ status: "active", cancel_at_period_end: true });
  const reactivate = await subscriberPage.evaluate(async ({ apiBase, id }) => {
    const response = await fetch(`${apiBase}/api/v1/subscriptions/${id}/auto-renew`, { method: "PATCH", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: true }) });
    return { status: response.status, body: await response.json() };
  }, { apiBase, id: subscription.body[0].id });
  expect(reactivate.body).toMatchObject({ status: "active", auto_renew: true, cancel_at_period_end: false });
  const lockedPpv = await subscriberPage.evaluate(async ({ apiBase, contentId }) => {
    const response = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, { credentials: "include" });
    return response.json();
  }, { apiBase, contentId: content.id });
  expect(lockedPpv.has_access).toBe(false);
  await subscriberContext.close();
  const earnings = await page.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/finance/creator/earnings?currency=EUR`, { credentials: "include" });
    return { status: response.status, body: await response.json() };
  }, { apiBase });
  expect(earnings.status).toBe(200);
  expect(earnings.body).toMatchObject({ pending_amount_minor: 1600, ppv_gross_amount_minor: 999, platform_fee_amount_minor: 199, creator_net_amount_minor: 800, currency: "EUR" });
  const anonymous = await browser.newContext();
  const anonymousPage = await anonymous.newPage();
  await anonymousPage.goto(`http://127.0.0.1:31000/creator/${username}`);
  await expect(anonymousPage.getByText(galleryTitle)).toBeVisible();
  await expect(anonymousPage.locator("img")).toHaveCount(1);
  const previewUrl = await anonymousPage.locator("img").getAttribute("src");
  expect(previewUrl).toContain("/media/previews/");
  expect(previewUrl).not.toContain("original/");
  await anonymous.close();

  await page.goto("/creator-studio");
  await page.getByLabel("Upload image or video").setInputFiles({ name: "video.mp4", mimeType: "video/mp4", buffer: videoFixture() });
  await page.getByRole("button", { name: "Upload media" }).click();
  await expect.poll(async () => page.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/media/mine`, { credentials: "include" });
    return (await response.json() as { status: string }[]).filter(asset => asset.status === "ready").length;
  }, { apiBase }), { timeout: 30000 }).toBeGreaterThan(1);
});
