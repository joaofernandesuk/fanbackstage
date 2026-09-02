import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { completeRegistrationCompliance, expectAuthenticatedAs } from "./auth-helpers";
import { mailpitMessage, securityLink } from "./mailpit";

const apiBase =
  process.env.E2E_API_URL ?? process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };
const image = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);
const phase15Harness = join(process.cwd(), "../api/tests/e2e_phase15_notification_harness.py");
const phase15Python = join(process.cwd(), "../api/.venv/bin/python");

function phase15Receipt(email: string, purchaseId: string) {
  return JSON.parse(execFileSync(phase15Python, [phase15Harness, "receipt", email, purchaseId], {
    env: { ...process.env, FANBACKSTAGE_E2E_RELEASE_VALIDATION: "1" }, encoding: "utf8",
  })) as { intent_count: number; payload: { body: string } | null; attempt_count: number; statuses: string[]; payment_attempt_id: string };
}

function videoFixture(): Buffer {
  const directory = mkdtempSync(join(tmpdir(), "fanbackstage-phase2-"));
  const path = join(directory, "video.mp4");
  execFileSync("ffmpeg", ["-y", "-f", "lavfi", "-i", "color=c=purple:s=32x24:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", path], { stdio: "ignore" });
  return readFileSync(path);
}

async function login(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);
}

async function approve(page: import("@playwright/test").Page, path: string) {
  const response = await page.evaluate(async ({ apiBase, path }) => {
    const result = await fetch(`${apiBase}/api/v1${path}`, { method: "POST", credentials: "include" });
    return { status: result.status, body: await result.json() };
  }, { apiBase, path });
  expect(response.status).toBe(200);
}

async function selectReadyImages(
  page: import("@playwright/test").Page,
  selectedIndexes: number[],
) {
  const readyImages = page
    .getByRole("group", { name: "Ready images" })
    .getByRole("checkbox");
  await expect.poll(() => readyImages.count(), { timeout: 30_000 }).toBeGreaterThanOrEqual(7);
  const checkboxes = await readyImages.all();
  for (const checkbox of checkboxes) {
    if (await checkbox.isChecked()) await checkbox.uncheck();
  }
  for (const index of selectedIndexes) await checkboxes[index].check();
}

test("creator media travels through the real private processing stack", async ({ browser, page }) => {
  const stamp = Date.now();
  const email = `phase2-media-${stamp}@example.com`;
  const password = "phase2-media-password";
  const username = `media${stamp}`;
  const galleryTitle = `Private gallery ${stamp}`;
  const subscriptionTitle = `Subscriber gallery ${stamp}`;
  const freeGalleryTitle = `Free gallery ${stamp}`;
  const videoTitle = `Private video ${stamp}`;

  await page.goto("/register");
  await completeRegistrationCompliance(page);
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check();
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
  await page.getByRole("checkbox", { name: "Make my approved creator profile public" }).check();
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => page.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/creators/me`, { credentials: "include" });
    return await response.json();
  }, { apiBase }), { timeout: 15_000 }).toMatchObject({ status: "approved", is_public: true });
  await page.goto("/creator-studio");
  await page.getByRole("button", { name: /^Media library / }).click();
  await expect(page.getByRole("heading", { name: "Upload media" })).toBeVisible();
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
  await page.locator('input[type="file"]').setInputFiles({ name: "gallery.png", mimeType: "image/png", buffer: image });
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
  for (const [index, name] of [
    "subscriber-first.png",
    "subscriber-second.png",
    "subscriber-third.png",
    "free-first.png",
    "free-second.png",
    "free-third.png",
  ].entries()) {
    await page.locator('input[type="file"]').setInputFiles({
      name,
      mimeType: "image/png",
      buffer: image,
    });
    await page.getByRole("button", { name: "Upload media" }).click();
    await expect.poll(async () => page.evaluate(async ({ apiBase }) => {
      const response = await fetch(`${apiBase}/api/v1/media/mine`, { credentials: "include" });
      return (await response.json() as { status: string }[])
        .filter(asset => asset.status === "ready").length;
    }, { apiBase }), { timeout: 30000 }).toBeGreaterThan(index + 1);
  }
  await expect.poll(() => storageRequests.length, { timeout: 10000 }).toBe(7);
  await page.reload();
  await selectReadyImages(page, [0]);
  await page.getByLabel("Gallery title").fill(galleryTitle);
  // The media workspace owns the gallery and video policy controls. Keep the
  // gallery journey scoped to the first control in that authoritative panel.
  await page.getByLabel("Access policy").first().selectOption("ppv");
  await page.getByLabel("PPV price (minor units)").first().fill("999");
  await page.getByRole("button", { name: "Create and submit gallery" }).click();
  await expect(page.getByText(/pending_review/)).toBeVisible();
  await selectReadyImages(page, [1, 2, 3]);
  await page.getByLabel("Gallery title").fill(subscriptionTitle);
  await page.getByLabel("Access policy").first().selectOption("subscription");
  await page.getByRole("button", { name: "Create and submit gallery" }).click();
  await expect(page.getByText(/pending_review/)).toHaveCount(2);
  await selectReadyImages(page, [4, 5, 6]);
  await page.getByLabel("Gallery title").fill(freeGalleryTitle);
  await page.getByLabel("Access policy").first().selectOption("free");
  await page.getByRole("button", { name: "Create and submit gallery" }).click();
  await expect(page.getByText(/pending_review/)).toHaveCount(3);

  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();
  await login(page, admin.email, admin.password);
  const published = await page.evaluate(async ({ apiBase, galleryTitle, subscriptionTitle, freeGalleryTitle }) => {
    const result = await fetch(`${apiBase}/api/v1/admin/content-review`, { credentials: "include" });
    const items = await result.json();
    return [galleryTitle, subscriptionTitle, freeGalleryTitle]
      .map(title => items.find((item: { title: string }) => item.title === title));
  }, { apiBase, galleryTitle, subscriptionTitle, freeGalleryTitle });
  expect(published).toHaveLength(3);
  expect(published.every(Boolean)).toBe(true);
  for (const item of published) await approve(page, `/admin/content-review/${item.id}/approve`);
  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();

  await login(page, email, password);
  const owner = await page.evaluate(async ({ apiBase, contentId }) => {
    const result = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, { credentials: "include" });
    return await result.json();
  }, { apiBase, contentId: published[0].id });
  expect(owner.has_access).toBe(true);
  expect(owner.media_count).toBe(1);
  expect(owner.media.map((item: { position: number }) => item.position)).toEqual([0]);
  expect(JSON.stringify(owner)).not.toContain("original/");
  await page.goto("/creator-studio");
  await page.getByRole("button", { name: /^Audience / }).click();
  await page.getByLabel("Enable subscriptions").check();
  for (const [index, price] of ["1000", "3000", "6000", "12000"].entries()) {
    await page.getByLabel("Price (minor units)").nth(index).fill(price);
  }
  await page.getByRole("button", { name: "Save subscription plan" }).click();
  await expect(page.getByText("Subscription plan saved.")).toBeVisible();
  await page.getByLabel("Promotion name").fill(`Launch ${stamp}`);
  await page.getByLabel("Starts at", { exact: true }).fill(new Date(Date.now() - 60_000).toISOString().slice(0, 16));
  await page.getByRole("group", { name: "1 month" }).getByLabel("Include duration").check();
  await page.getByRole("group", { name: "1 month" }).getByLabel("Discount (basis points)").fill("2000");
  await page.getByRole("button", { name: "Create subscription promotion" }).click();
  await expect(page.getByText("Subscription promotion created.")).toBeVisible();
  const buyerContext = await browser.newContext();
  const buyerPage = await buyerContext.newPage();
  const buyerEmail = `phase3-buyer-${stamp}@example.com`;
  await buyerPage.goto("/register");
  await completeRegistrationCompliance(buyerPage);
  await buyerPage.getByLabel("Email").fill(buyerEmail);
  await buyerPage.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await buyerPage.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check();
  await buyerPage.getByRole("button", { name: "Create account" }).click();
  await buyerPage.goto(await securityLink(buyerEmail, "/verify-email"));
  await buyerPage.getByRole("button", { name: "Verify email" }).click();
  await login(buyerPage, buyerEmail, password);
  await buyerPage.goto(`/creator/${username}`);
  await buyerPage.getByRole("tab", { name: "Premium" }).click();
  await buyerPage.getByRole("link").filter({ hasText: galleryTitle }).click();
  await buyerPage.getByRole("button", { name: "Unlock for €9.99" }).click();
  await buyerPage.getByRole("button", { name: "Confirm €9.99" }).click();
  await expect(buyerPage.getByRole("button", { name: "Unlock for €9.99" })).toHaveCount(0);
  await expect.poll(async () => buyerPage.evaluate(async ({ apiBase, contentId }) => {
    const response = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, { credentials: "include" });
    return (await response.json()).has_access;
  }, { apiBase, contentId: published[0].id }), { timeout: 10_000 }).toBe(true);
  await buyerPage.goto(`/creator/${username}`);
  await buyerPage.getByRole("tab", { name: "Photos" }).click();
  await expect(buyerPage.getByRole("img", { name: `${galleryTitle} preview` })).toBeVisible();
  const purchases = await buyerPage.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/purchases/mine`, { credentials: "include" });
    return { status: response.status, body: await response.json() };
  }, { apiBase });
  expect(purchases.status).toBe(200);
  expect(purchases.body).toHaveLength(1);
  expect(purchases.body[0]).toMatchObject({ gross_amount_minor: 999, currency: "EUR", status: "paid" });
  // Replaying the authoritative payment completion must return the settled
  // purchase rather than create another financial event or receipt intent.
  const receiptBeforeReplay = phase15Receipt(buyerEmail, purchases.body[0].id);
  const duplicatePayment = await buyerPage.evaluate(async ({ apiBase, paymentAttemptId }) => {
    const response = await fetch(`${apiBase}/api/v1/payments/development/${paymentAttemptId}/complete`, {
      method: "POST", credentials: "include",
    });
    return { status: response.status, body: await response.json() };
  }, { apiBase, paymentAttemptId: receiptBeforeReplay.payment_attempt_id });
  expect(duplicatePayment.status).toBe(200);
  await expect.poll(() => phase15Receipt(buyerEmail, purchases.body[0].id).statuses[0], { timeout: 10_000 }).toBe("sent");
  const receipt = phase15Receipt(buyerEmail, purchases.body[0].id);
  expect(receipt).toMatchObject({ intent_count: 1, attempt_count: 1, statuses: ["sent"] });
  expect(receipt.payload?.body).toBe("Your purchase of 999 EUR is confirmed.");
  const receiptMail = await mailpitMessage(buyerEmail, "Your purchase of 999 EUR is confirmed.");
  expect(receiptMail.Subject).toBe("Purchase receipt");
  await buyerContext.close();
  const subscriberContext = await browser.newContext();
  const subscriberPage = await subscriberContext.newPage();
  const subscriberEmail = `phase4-subscriber-${stamp}@example.com`;
  await subscriberPage.goto("/register");
  await completeRegistrationCompliance(subscriberPage);
  await subscriberPage.getByLabel("Email").fill(subscriberEmail);
  await subscriberPage.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await subscriberPage.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check();
  await subscriberPage.getByRole("button", { name: "Create account" }).click();
  await subscriberPage.goto(await securityLink(subscriberEmail, "/verify-email"));
  await subscriberPage.getByRole("button", { name: "Verify email" }).click();
  await login(subscriberPage, subscriberEmail, password);
  await subscriberPage.goto(`/creator/${username}`);
  await subscriberPage.getByRole("tab", { name: "Premium" }).click();
  await expect(subscriberPage.getByText("€8.00", { exact: true })).toBeVisible();
  await subscriberPage.locator('section[aria-label="Subscriptions"] article').filter({ hasText: "€8.00" }).getByRole("button", { name: "Choose 1 month" }).click();
  await subscriberPage.getByRole("button", { name: "Confirm €8.00" }).click();
  await expect(subscriberPage.getByText("1 month subscription is active.")).toBeVisible();
  await subscriberPage.reload();
  await subscriberPage.getByRole("tab", { name: "Premium" }).click();
  await expect(subscriberPage.getByText(subscriptionTitle)).toBeVisible();
  await expect(subscriberPage.getByText("Preview · €9.99")).toBeVisible();
  const subscription = await subscriberPage.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/subscriptions/mine`, { credentials: "include" });
    return { status: response.status, body: await response.json() };
  }, { apiBase });
  expect(subscription.status).toBe(200);
  expect(subscription.body).toHaveLength(1);
  expect(subscription.body[0]).toMatchObject({ duration: "month_1", status: "active", auto_renew: true });
  await subscriberPage.goto("/subscriptions");
  await subscriberPage.getByRole("button", { name: "Cancel at period end" }).click();
  await expect(subscriberPage.getByText("Subscription will remain active until the current period ends.")).toBeVisible();
  await subscriberPage.getByRole("button", { name: "Reactivate subscription" }).click();
  await expect(subscriberPage.getByText("Subscription reactivated.")).toBeVisible();
  const lockedPpv = await subscriberPage.evaluate(async ({ apiBase, contentId }) => {
    const response = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, { credentials: "include" });
    return response.json();
  }, { apiBase, contentId: published[0].id });
  expect(lockedPpv.has_access).toBe(false);
  const subscriptionContent = await subscriberPage.evaluate(async ({ apiBase, contentId }) => {
    const response = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, { credentials: "include" });
    return response.json();
  }, { apiBase, contentId: published[1].id });
  expect(subscriptionContent.has_access).toBe(true);
  expect(subscriptionContent.media.map((item: { position: number }) => item.position))
    .toEqual([0, 1, 2]);
  await subscriberPage.goto(`/content/${published[1].id}`);
  await expect(subscriberPage.getByRole("heading", { name: subscriptionTitle })).toBeVisible();
  await expect(subscriberPage.getByRole("region", { name: `${subscriptionTitle} gallery` }))
    .toBeVisible();
  await expect(
    subscriberPage.getByRole("group", { name: "Gallery thumbnails" }).getByRole("button"),
  ).toHaveCount(3);
  await subscriberContext.close();
  const earnings = await page.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/finance/creator/earnings?currency=EUR`, { credentials: "include" });
    return { status: response.status, body: await response.json() };
  }, { apiBase });
  expect(earnings.status).toBe(200);
  // The PPV settlement credits 800 and the promotion-priced subscription credits 640.
  // Keep the PPV-specific totals separate: subscription revenue shares the immutable
  // creator-pending ledger account but must not be reported as PPV revenue.
  expect(earnings.body).toMatchObject({ pending_amount_minor: 1440, ppv_gross_amount_minor: 999, platform_fee_amount_minor: 199, creator_net_amount_minor: 800, currency: "EUR" });
  const anonymous = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const anonymousPage = await anonymous.newPage();
  await anonymousPage.goto(
    `${process.env.E2E_WEB_URL ?? "http://127.0.0.1:38181"}/content/${published[2].id}`,
  );
  await expect(anonymousPage.getByRole("heading", { name: "Age-restricted content", exact: true })).toBeVisible();
  await expect(anonymousPage.getByRole("region", { name: `${freeGalleryTitle} gallery` }))
    .toHaveCount(0);
  await anonymousPage.getByRole("button", { name: "Verify age" }).click();
  await expect(anonymousPage.getByRole("heading", { name: freeGalleryTitle, exact: true })).toBeVisible();
  await expect(anonymousPage.getByRole("region", { name: `${freeGalleryTitle} gallery` }))
    .toBeVisible();
  await anonymousPage.goto(`${process.env.E2E_WEB_URL ?? "http://127.0.0.1:38181"}/creator/${username}`);
  await anonymousPage.getByRole("tab", { name: "Premium" }).click();
  await expect(anonymousPage.getByText(galleryTitle)).toBeVisible();
  await expect(anonymousPage.getByText(subscriptionTitle)).toBeVisible();
  const publicPreviews = anonymousPage.locator('img[alt$=" preview"]');
  await expect(publicPreviews).toHaveCount(2);
  for (const previewUrl of await publicPreviews.evaluateAll(images =>
    images.map(image => image.getAttribute("src")),
  )) {
    expect(previewUrl).toContain("/media/previews/");
    expect(previewUrl).not.toContain("original/");
  }
  await anonymousPage.getByRole("tab", { name: "Photos" }).click();
  await expect(anonymousPage.getByText(freeGalleryTitle)).toBeVisible();
  await anonymousPage.goto("/galleries");
  const galleryCard = anonymousPage.locator("article").filter({ hasText: freeGalleryTitle });
  await expect(galleryCard).toBeVisible();
  await expect(galleryCard.getByText("3 photos", { exact: true })).toBeVisible();
  await anonymousPage.goto(
    `${process.env.E2E_WEB_URL ?? "http://127.0.0.1:38181"}/content/${published[2].id}`,
  );
  await expect(anonymousPage.getByRole("heading", { name: freeGalleryTitle, exact: true })).toBeVisible();
  await expect(anonymousPage.getByRole("region", { name: `${freeGalleryTitle} gallery` }))
    .toBeVisible();
  const freeGallery = await anonymousPage.evaluate(async ({ apiBase, contentId }) => {
    const response = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, {
      credentials: "include",
    });
    return response.json();
  }, { apiBase, contentId: published[2].id });
  expect(freeGallery).toMatchObject({ has_access: true, media_count: 3 });
  expect(freeGallery.media.map((item: { position: number }) => item.position))
    .toEqual([0, 1, 2]);
  const thumbnails = anonymousPage
    .getByRole("group", { name: "Gallery thumbnails" })
    .getByRole("button");
  await expect(thumbnails).toHaveCount(3);
  await anonymousPage.getByRole("button", { name: "View image 3" }).click();
  await expect(anonymousPage.getByRole("img", { name: `${freeGalleryTitle} image 3` }))
    .toBeVisible();
  await anonymousPage
    .getByRole("button", { name: `Open ${freeGalleryTitle} image 3 full screen` })
    .click();
  const lightbox = anonymousPage.getByRole("dialog", {
    name: `${freeGalleryTitle} full-screen gallery`,
  });
  await expect(lightbox).toBeVisible();
  await expect(lightbox.getByRole("button", { name: "Close full-screen gallery" }))
    .toBeFocused();
  await anonymousPage.keyboard.press("ArrowLeft");
  await expect(lightbox.getByRole("img", { name: `${freeGalleryTitle} full-screen image 2` }))
    .toBeVisible();
  await anonymousPage.keyboard.press("Escape");
  await expect(lightbox).toBeHidden();
  expect(await anonymousPage.evaluate(() =>
    document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
  )).toBe(true);

  const protectedDelivery = await anonymousPage.evaluate(async ({ apiBase, contentId }) => {
    const contentResponse = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, {
      credentials: "include",
    });
    const content = await contentResponse.json();
    const derivativeId = content.previews[0]?.derivative_id;
    const derivative = derivativeId
      ? await fetch(`${apiBase}/api/v1/media/derivatives/${derivativeId}`, {
        credentials: "include",
        redirect: "manual",
      })
      : null;
    const original = derivativeId
      ? await fetch(`${apiBase}/api/v1/media/originals/${derivativeId}`, {
        credentials: "include",
        redirect: "manual",
      })
      : null;
    return {
      content,
      derivativeStatus: derivative?.status ?? null,
      originalStatus: original?.status ?? null,
    };
  }, { apiBase, contentId: published[0].id });
  expect(JSON.stringify(protectedDelivery.content)).not.toContain("original/");
  expect(protectedDelivery.derivativeStatus).toBe(404);
  expect(protectedDelivery.originalStatus).toBe(404);
  await anonymous.close();

  await page.goto("/creator-studio");
  await page.getByRole("button", { name: /^Media library / }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "video.mp4", mimeType: "video/mp4", buffer: videoFixture() });
  await page.getByRole("button", { name: "Upload media" }).click();
  await expect.poll(async () => page.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/media/mine`, { credentials: "include" });
    return (await response.json() as { status: string }[]).filter(asset => asset.status === "ready").length;
  }, { apiBase }), { timeout: 30000 }).toBeGreaterThan(7);
  await page.reload();
  await page.getByLabel("Ready video").selectOption({ index: 1 });
  await page.getByLabel("Video title").fill(videoTitle);
  await page.getByLabel("Access policy").nth(1).selectOption("ppv");
  await page.getByLabel("PPV price (minor units)").nth(1).fill("499");
  await page.getByLabel("Preview duration (seconds)").fill("1");
  await page.getByRole("button", { name: "Create video" }).click();
  await expect.poll(async () => page.evaluate(async ({ apiBase, videoTitle }) => {
    const response = await fetch(`${apiBase}/api/v1/content/mine`, { credentials: "include" });
    return (await response.json() as { id: string; title: string }[]).some(item => item.title === videoTitle);
  }, { apiBase, videoTitle }), { timeout: 15_000 }).toBe(true);
  const createdVideo = await page.evaluate(async ({ apiBase, videoTitle }) => {
    const response = await fetch(`${apiBase}/api/v1/content/mine`, { credentials: "include" });
    return (await response.json() as { id: string; title: string }[]).find(item => item.title === videoTitle);
  }, { apiBase, videoTitle });
  expect(createdVideo).toBeTruthy();
  await expect.poll(async () => page.evaluate(async ({ apiBase, contentId }) => {
    const response = await fetch(`${apiBase}/api/v1/content/${contentId}/submit`, {
      method: "POST", credentials: "include",
    });
    return response.status;
  }, { apiBase, contentId: createdVideo!.id }), { timeout: 30_000 }).toBe(200);

  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();
  await login(page, admin.email, admin.password);
  const videoReview = await page.evaluate(async ({ apiBase, videoTitle }) => {
    const response = await fetch(`${apiBase}/api/v1/admin/content-review`, { credentials: "include" });
    return (await response.json() as { id: string; title: string }[]).find(item => item.title === videoTitle);
  }, { apiBase, videoTitle });
  expect(videoReview).toBeTruthy();
  await approve(page, `/admin/content-review/${videoReview!.id}/approve`);

  const publicVideoContext = await browser.newContext();
  const publicVideoPage = await publicVideoContext.newPage();
  await publicVideoPage.goto(`${process.env.E2E_WEB_URL ?? "http://127.0.0.1:38181"}/content/${videoReview!.id}`);
  await expect(publicVideoPage.getByRole("heading", { name: "Age-restricted content", exact: true })).toBeVisible();
  await publicVideoPage.getByRole("button", { name: "Verify age" }).click();
  await expect(publicVideoPage.getByRole("heading", { name: videoTitle, exact: true })).toBeVisible();
  await expect(publicVideoPage.locator(`video[aria-label="${videoTitle} preview trailer"]`)).toBeVisible();
  const publicVideo = await publicVideoPage.evaluate(async ({ apiBase, contentId }) => {
    const response = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, { credentials: "include" });
    return response.json();
  }, { apiBase, contentId: videoReview!.id });
  expect(publicVideo).toMatchObject({ has_access: false, duration_seconds: 2 });
  expect(publicVideo.previews.find((item: { kind: string }) => item.kind === "trailer"))
    .toMatchObject({ duration_seconds: 1 });
  expect(publicVideo.media).toEqual([]);
  await expect(
    publicVideoPage.getByRole("heading", { name: "More from Media E2E Creator" }),
  ).toBeVisible();
  await expect(publicVideoPage.getByRole("link", { name: new RegExp(freeGalleryTitle) }))
    .toBeVisible();
  await publicVideoPage.goto("/videos");
  const videoCard = publicVideoPage.locator("article").filter({ hasText: videoTitle });
  await expect(videoCard).toBeVisible();
  await expect(videoCard.getByText("0:02", { exact: true })).toBeVisible();
  await publicVideoContext.close();

  const videoBuyerContext = await browser.newContext();
  const videoBuyerPage = await videoBuyerContext.newPage();
  await login(videoBuyerPage, buyerEmail, password);
  await videoBuyerPage.goto(`/content/${videoReview!.id}`);
  await videoBuyerPage.getByRole("button", { name: "Unlock for €4.99" }).click();
  await videoBuyerPage.getByRole("button", { name: "Confirm €4.99" }).click();
  await expect(videoBuyerPage.getByText("FULL VIDEO")).toBeVisible();
  const purchasedVideo = await videoBuyerPage.evaluate(async ({ apiBase, contentId }) => {
    const response = await fetch(`${apiBase}/api/v1/content/public/${contentId}`, {
      credentials: "include",
    });
    return response.json();
  }, { apiBase, contentId: videoReview!.id });
  expect(purchasedVideo.has_access).toBe(true);
  expect(purchasedVideo.media.some((item: { kind: string }) => item.kind === "playback"))
    .toBe(true);
  await videoBuyerContext.close();
  // A later commission-policy revision is intentionally forward-looking: it
  // cannot rewrite the settled purchase snapshot or its already-rendered receipt.
  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();
  await login(page, admin.email, admin.password);
  const previousCommission = await page.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/admin/finance/commission`, { credentials: "include" });
    return { status: response.status, body: await response.json() };
  }, { apiBase });
  expect(previousCommission.status).toBe(200);
  const commission = await page.evaluate(async ({ apiBase, previousCommission }) => {
    const response = await fetch(`${apiBase}/api/v1/admin/finance/commission`, {
      method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ basis_points: previousCommission.body.basis_points === 2500 ? 2000 : 2500 }),
    });
    return { status: response.status, body: await response.json() };
  }, { apiBase, previousCommission });
  expect(commission.status).toBe(200);
  expect(phase15Receipt(buyerEmail, purchases.body[0].id).payload?.body)
    .toBe("Your purchase of 999 EUR is confirmed.");
  const restoredCommission = await page.evaluate(async ({ apiBase, basisPoints }) => {
    const response = await fetch(`${apiBase}/api/v1/admin/finance/commission`, {
      method: "PUT", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ basis_points: basisPoints }),
    });
    return response.status;
  }, { apiBase, basisPoints: previousCommission.body.basis_points });
  expect(restoredCommission).toBe(200);
});
