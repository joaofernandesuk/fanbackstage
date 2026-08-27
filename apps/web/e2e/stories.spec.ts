import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

import { expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = {
  email: "phase2-e2e-admin@example.com",
  password: "phase2-e2e-admin-password",
};
const image = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);
const storyHarness = join(process.cwd(), "../api/tests/e2e_story_harness.py");
const storyPython = join(process.cwd(), "../api/.venv/bin/python");

type ApiResult<T> = { status: number; body: T };
type Story = {
  id: string;
  status: string;
  expires_at: string;
  media: { delivery_path: string };
};

async function api<T>(
  page: Page,
  path: string,
  method = "GET",
  body?: unknown,
  headers?: Record<string, string>,
): Promise<ApiResult<T>> {
  return page.evaluate(async ({ apiBase, path, method, body, headers }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, {
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json", ...headers } : headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    return {
      status: response.status,
      body: await response.json().catch(() => null) as T,
    };
  }, { apiBase, path, method, body, headers });
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
  await page.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check();
  await page.getByRole("button", { name: "Create account" }).click();
  await page.goto(await securityLink(email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await login(page, email, password);
}

function expireStory(storyId: string) {
  return JSON.parse(execFileSync(storyPython, [storyHarness, "expire", storyId], {
    encoding: "utf8",
    env: { ...process.env, FANBACKSTAGE_E2E_STORY_VALIDATION: "1" },
  })) as { expired: number; target_status: string };
}

test("active Stories render from safe derivatives and expired Stories stay absent", async ({
  browser,
  page,
}) => {
  const stamp = Date.now();
  const email = `stories-creator-${stamp}@example.com`;
  const password = "stories-creator-password";
  const username = `stories${stamp}`;
  const displayName = "Story E2E Creator";

  await register(page, email, password);
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Display name").fill(displayName);
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api<{ username: string }>(page, "/creators/me")).body.username, {
    timeout: 15_000,
  }).toBe(username);
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect.poll(async () => (await api<{ status: string }>(page, "/creators/me")).body.status, {
    timeout: 15_000,
  }).toBe("pending_verification");
  await page.getByRole("button", { name: "Complete development verification" }).click();
  await expect.poll(async () => (await api<{ status: string }>(page, "/creators/me")).body.status, {
    timeout: 15_000,
  }).toBe("pending_review");

  await login(page, admin.email, admin.password);
  const applications = await api<{ id: string; username: string }[]>(
    page,
    "/admin/creator-applications",
  );
  const application = applications.body.find((item) => item.username === username);
  expect(application).toBeTruthy();
  expect((await api(page, `/admin/creator-applications/${application!.id}/approve`, "POST")).status).toBe(200);

  await login(page, email, password);
  await page.goto("/creator-onboarding");
  await page.getByRole("checkbox", { name: "Make my approved creator profile public" }).check();
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => (await api(page, "/creators/me")).body, {
    timeout: 15_000,
  }).toMatchObject({ status: "approved", is_public: true });

  const upload = await api<{
    id: string;
    status: string;
    upload_url: string | null;
  }>(page, "/media/uploads", "POST", {
    filename: "story.png",
    mime_type: "image/png",
  });
  expect(upload.status, JSON.stringify(upload.body)).toBe(200);
  expect(upload.body.upload_url).toBeTruthy();
  const stored = await fetch(upload.body.upload_url!, {
    method: "PUT",
    headers: { "Content-Type": "image/png" },
    body: image,
  });
  expect(stored.status).toBe(200);
  const finalized = await api<{ status: string }>(
    page,
    `/media/${upload.body.id}/finalize`,
    "POST",
  );
  expect(finalized.status, JSON.stringify(finalized.body)).toBe(200);
  expect(finalized.body.status).toBe("queued");
  await expect.poll(async () => {
    const assets = await api<{ id: string; status: string }[]>(page, "/media/mine");
    return assets.body.find((asset) => asset.id === upload.body.id)?.status;
  }, { timeout: 30_000 }).toBe("ready");

  const expired = await api<Story>(
    page,
    "/stories",
    "POST",
    {
      media_asset_id: upload.body.id,
      caption: "This Story must expire",
      alt_text: "Expired Story image",
      access_policy: "free",
    },
    { "Idempotency-Key": `story-expired-${stamp}` },
  );
  expect(expired.status, JSON.stringify(expired.body)).toBe(201);
  await new Promise((resolve) => setTimeout(resolve, 25));
  const first = await api<Story>(
    page,
    "/stories",
    "POST",
    {
      media_asset_id: upload.body.id,
      caption: "First active Story",
      alt_text: "First active Story image",
      access_policy: "free",
    },
    { "Idempotency-Key": `story-first-${stamp}` },
  );
  expect(first.status, JSON.stringify(first.body)).toBe(201);
  await new Promise((resolve) => setTimeout(resolve, 25));
  const second = await api<Story>(
    page,
    "/stories",
    "POST",
    {
      media_asset_id: upload.body.id,
      caption: "Second active Story",
      alt_text: "Second active Story image",
      access_policy: "free",
    },
    { "Idempotency-Key": `story-second-${stamp}` },
  );
  expect(second.status, JSON.stringify(second.body)).toBe(201);
  expect(Date.parse(expired.body.expires_at)).toBeLessThan(Date.parse(first.body.expires_at));
  expect(Date.parse(first.body.expires_at)).toBeLessThan(Date.parse(second.body.expires_at));

  const expiry = expireStory(expired.body.id);
  expect(expiry.expired).toBeGreaterThanOrEqual(1);
  expect(expiry.target_status).toBe("expired");

  const viewerContext = await browser.newContext();
  const viewer = await viewerContext.newPage();
  const adultAccessResponse = await viewer.request.post(
    `${apiBase}/api/v1/auth/adult-access`,
    { data: { adult_confirmed: true } },
  );
  expect(adultAccessResponse.status()).toBe(200);
  expect(await adultAccessResponse.json()).toMatchObject({
    allowed: true,
    assurance: "self_attested",
    source: "cookie",
  });
  const railResponse = await viewer.request.get(
    `${apiBase}/api/v1/stories/rail?creator_username=${encodeURIComponent(username)}`,
  );
  expect(railResponse.status()).toBe(200);
  const rail = await railResponse.json() as { items: Story[] };
  expect(rail.items.map((story) => story.id)).toEqual([second.body.id, first.body.id]);
  expect(JSON.stringify(rail)).not.toContain("original/");
  expect(rail.items.every((story) => story.media.delivery_path === `/stories/${story.id}/media`)).toBe(true);
  const mediaRedirect = await viewer.request.get(
    `${apiBase}/api/v1/stories/${second.body.id}/media`,
    { maxRedirects: 0 },
  );
  expect(mediaRedirect.status()).toBe(307);
  const mediaLocation = mediaRedirect.headers().location ?? "";
  expect(mediaLocation).toContain("/derivative/");
  expect(mediaLocation).not.toContain("/original/");
  const expiredDetail = await viewer.request.get(
    `${apiBase}/api/v1/stories/${expired.body.id}`,
  );
  expect(expiredDetail.status()).toBe(404);

  await viewer.goto("/stories");
  await expect(viewer.getByRole("list", { name: "Creator stories" })).toBeVisible();
  const opener = viewer.getByRole("button", {
    name: `Open ${displayName}'s 2 stories`,
  });
  await expect(opener).toBeVisible();
  const firstMedia = viewer.waitForResponse((response) =>
    response.url().includes(`/api/v1/stories/${second.body.id}/media`),
  );
  await opener.click();
  await firstMedia;
  const dialog = viewer.getByRole("dialog", { name: `${displayName} story` });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("status", { name: "Story 1 of 2" })).toBeVisible();
  await expect(dialog.getByRole("img", { name: "Second active Story image" })).toBeVisible();
  const profileLink = dialog.getByRole("link", { name: "View profile", exact: true });
  await expect(profileLink).toHaveAttribute("href", `/creator/${username}`);

  const secondMedia = viewer.waitForResponse((response) =>
    response.url().includes(`/api/v1/stories/${first.body.id}/media`),
  );
  await dialog.getByRole("button", { name: "Next story" }).click();
  await secondMedia;
  await expect(dialog.getByRole("status", { name: "Story 2 of 2" })).toBeVisible();
  await expect(dialog.getByRole("img", { name: "First active Story image" })).toBeVisible();
  await profileLink.click();
  await expect(viewer).toHaveURL(`/creator/${username}`);
  await expect(viewer.getByRole("heading", { name: displayName })).toBeVisible();
  await viewerContext.close();
});
