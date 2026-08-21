import { expect, test } from "@playwright/test";

import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };

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

async function api(page: import("@playwright/test").Page, path: string, method = "GET", body?: unknown) {
  return page.evaluate(async ({ apiBase, path, method, body }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, {
      method, credentials: "include", headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: response.status, body: await response.json() };
  }, { apiBase, path, method, body });
}

test("Phase 5 social access, engagement, and report moderation use the real stack", async ({ browser, page }) => {
  const stamp = Date.now();
  const password = "phase5-social-password";
  const creatorEmail = `phase5-creator-${stamp}@example.com`;
  const creatorUsername = `social${stamp}`;
  await register(page, creatorEmail, password);
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByLabel("Username").fill(creatorUsername);
  await page.getByLabel("Display name").fill("Phase 5 Creator");
  await page.getByRole("button", { name: "Save profile" }).click();
  await page.getByRole("button", { name: "Submit application" }).click();
  await page.getByRole("button", { name: "Complete development verification" }).click();
  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();

  await login(page, admin.email, admin.password);
  const applications = await api(page, "/admin/creator-applications");
  const application = applications.body.find((item: { username: string }) => item.username === creatorUsername);
  expect(application).toBeTruthy();
  expect((await api(page, `/admin/creator-applications/${application.id}/approve`, "POST")).status).toBe(200);
  await page.getByRole("button", { name: "Log out" }).click();
  await login(page, creatorEmail, password);
  await page.goto("/creator-onboarding");
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect(page.getByRole("link", { name: "View public profile" })).toBeVisible();
  const publicProfile = await api(page, `/creators/${creatorUsername}`);
  expect(publicProfile).toMatchObject({ status: 200, body: { username: creatorUsername } });
  expect(typeof publicProfile.body.id).toBe("string");

  const free = await api(page, "/feed/posts", "POST", { post_type: "text", body: `free ${stamp}`, access_policy: "free" });
  expect((await api(page, `/feed/posts/${free.body.id}/publish`, "POST")).status).toBe(200);
  const followers = await api(page, "/feed/posts", "POST", { post_type: "text", body: `followers secret ${stamp}`, access_policy: "followers" });
  await api(page, `/feed/posts/${followers.body.id}/publish`, "POST");
  const subscription = await api(page, "/feed/posts", "POST", { post_type: "text", body: `subscription secret ${stamp}`, access_policy: "subscription" });
  await api(page, `/feed/posts/${subscription.body.id}/publish`, "POST");

  const followerContext = await browser.newContext(); const follower = await followerContext.newPage();
  const followerEmail = `phase5-follower-${stamp}@example.com`; await register(follower, followerEmail, password);
  const discover = await api(follower, "/feed/discover");
  const discovered = discover.body.items.find((item: { id: string }) => item.id === free.body.id);
  expect(discovered).toMatchObject({ body: `free ${stamp}`, creator_id: publicProfile.body.id });
  const creatorId = discovered.creator_id as string;
  const follow = await api(follower, `/feed/creator/${creatorId}/follow`, "POST");
  expect(follow, `follow URL=/api/v1/feed/creator/${creatorId}/follow creatorProfileId=${creatorId}`).toMatchObject({ status: 200 });
  expect((await api(follower, `/feed/creator/${creatorId}/follow`, "POST")).body).toMatchObject({ following: true, created: false });
  expect((await api(follower, "/feed/creator/not-a-uuid/follow", "POST")).status).toBe(422);
  expect((await api(follower, "/feed/creator/00000000-0000-0000-0000-000000000000/follow", "POST")).status).toBe(404);
  expect((await api(follower, "/feed/following")).body.items.some((item: { id: string }) => item.id === free.body.id)).toBe(true);
  expect((await api(follower, `/feed/posts/${followers.body.id}`)).body).toMatchObject({ locked: false, body: `followers secret ${stamp}` });
  const reaction = await api(follower, `/feed/posts/${free.body.id}/reaction`, "PUT", { reaction_type: "like" }); expect(reaction.status).toBe(200);
  await api(follower, `/feed/posts/${free.body.id}/reaction`, "PUT", { reaction_type: "like" });
  expect((await api(follower, `/feed/posts/${free.body.id}`)).body.reaction_count).toBe(1);
  const comment = await api(follower, `/feed/posts/${free.body.id}/comments`, "POST", { body: "comment" });
  const reply = await api(follower, `/feed/posts/${free.body.id}/comments`, "POST", { body: "reply", parent_id: comment.body.id });
  expect(reply.status).toBe(200);
  expect((await api(follower, `/feed/posts/${subscription.body.id}`)).body).toMatchObject({ locked: true, body: null });

  const strangerContext = await browser.newContext(); const stranger = await strangerContext.newPage();
  await register(stranger, `phase5-stranger-${stamp}@example.com`, password);
  const locked = await api(stranger, `/feed/posts/${followers.body.id}`); expect(locked.body).toMatchObject({ locked: true, body: null });
  expect((await api(stranger, `/feed/posts/${followers.body.id}/comments`, "POST", { body: "blocked" })).status).toBe(403);
  await followerContext.close(); await strangerContext.close();

  expect((await api(page, `/feed/comments/${comment.body.id}`, "DELETE")).status).toBe(200);
  expect((await api(page, `/feed/posts/${free.body.id}/pin`, "POST")).status).toBe(200);
  expect((await api(page, `/feed/posts/${free.body.id}/pin`, "DELETE")).status).toBe(200);
  const report = await api(page, `/feed/reports/post/${free.body.id}`, "POST", { reason: "test-report" }); expect(report.status).toBe(200);
  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click(); await login(page, admin.email, admin.password);
  const reports = await api(page, "/admin/social-reports?status=open"); const item = reports.body.find((row: { target_id: string }) => row.target_id === free.body.id);
  expect(item).toBeTruthy(); expect((await api(page, `/admin/social-reports/${item.id}/remove-target`, "POST")).status).toBe(200);
  await page.getByRole("button", { name: "Log out" }).click(); await login(page, creatorEmail, password);
  expect((await api(page, "/feed/discover")).body.items.some((row: { id: string }) => row.id === free.body.id)).toBe(false);
});
