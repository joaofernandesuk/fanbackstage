import { expect, test, type Page } from "@playwright/test";

import { expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const moderator = { email: "phase13-e2e-moderator@example.com", password: "phase13-e2e-moderator-password" };
const reviewer = { email: "phase13-e2e-reviewer@example.com", password: "phase13-e2e-reviewer-password" };
const admin = { email: "phase2-e2e-admin@example.com", password: "phase2-e2e-admin-password" };
const image = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");

type ApiResult<T> = { status: number; body: T };
type Creator = { email: string; password: string; username: string; id: string };
type Content = { id: string; title: string; status: string };

async function api<T>(page: Page, path: string, method = "GET", body?: unknown): Promise<ApiResult<T>> {
  return page.evaluate(async ({ apiBase, path, method, body }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, {
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json", ...(path === "/featuring/bookings" ? { "Idempotency-Key": `phase13-${Date.now()}` } : {}) } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { apiBase, path, method, body });
}

async function apiOk<T>(page: Page, path: string, method = "GET", body?: unknown): Promise<T> {
  const result = await api<T>(page, path, method, body);
  expect(result.status, `${method} ${path}: ${JSON.stringify(result.body)}`).toBe(200);
  return result.body;
}

async function login(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);
}

async function logout(page: Page) {
  await page.goto("/account");
  await page.getByRole("button", { name: "Log out" }).click();
}

async function register(page: Page, email: string, password: string) {
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.goto(await securityLink(email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await login(page, email, password);
}

async function createApprovedCreator(page: Page, stamp: string): Promise<Creator> {
  const password = "phase13-creator-password";
  const email = `phase13-${stamp}@example.com`;
  const username = `ts${stamp.replace(/\D/g, "").slice(-12)}`;
  await register(page, email, password);
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Display name").fill(`Trust Safety ${stamp}`);
  await page.getByRole("button", { name: "Save profile" }).click();
  await page.getByRole("button", { name: "Submit application" }).click();
  await page.getByRole("button", { name: "Complete development verification" }).click();
  await logout(page);
  await login(page, admin.email, admin.password);
  const applications = await apiOk<{ id: string; username: string }[]>(page, "/admin/creator-applications");
  const application = applications.find((item) => item.username === username);
  expect(application).toBeTruthy();
  await apiOk(page, `/admin/creator-applications/${application!.id}/approve`, "POST");
  await logout(page);
  await login(page, email, password);
  await page.goto("/creator-onboarding");
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect.poll(async () => await apiOk<{ status: string; is_public: boolean }>(page, "/creators/me"), { timeout: 15_000 }).toMatchObject({ status: "approved", is_public: true });
  const profile = await apiOk<{ id: string }>(page, "/creators/me");
  return { email, password, username, id: profile.id };
}

async function publishGallery(page: Page, creator: Creator, title: string, requiresConsent = false, approve = true): Promise<Content> {
  await page.goto("/creator-studio");
  await page.getByLabel("Upload image or video").setInputFiles({ name: `${title}.png`, mimeType: "image/png", buffer: image });
  await page.getByRole("button", { name: "Upload media" }).click();
  const asset = await expect.poll(async () => {
    const assets = await apiOk<{ id: string; status: string; media_type: string }[]>(page, "/media/mine");
    return assets.find((item) => item.status === "ready" && item.media_type === "image");
  }, { timeout: 30_000 }).toBeTruthy();
  void asset;
  const assets = await apiOk<{ id: string; status: string; media_type: string }[]>(page, "/media/mine");
  const ready = assets.find((item) => item.status === "ready" && item.media_type === "image");
  expect(ready).toBeTruthy();
  const gallery = await apiOk<Content>(page, "/content/galleries", "POST", { title, access_policy: "free", requires_verified_consent: requiresConsent });
  await apiOk(page, `/content/galleries/${gallery.id}/items`, "POST", { media_asset_id: ready!.id, is_preview: true });
  await apiOk(page, `/content/galleries/${gallery.id}/cover`, "PATCH", { media_asset_id: ready!.id });
  await apiOk(page, `/content/galleries/${gallery.id}/preview`, "PATCH", { preview_count: 1, preview_asset_ids: [] });
  await apiOk(page, `/content/${gallery.id}/submit`, "POST");
  if (!approve) return gallery;
  await logout(page);
  await login(page, admin.email, admin.password);
  await expect.poll(async () => (await apiOk<{ id: string; title: string }[]>(page, "/admin/content-review")).some((item) => item.id === gallery.id)).toBe(true);
  await apiOk(page, `/admin/content-review/${gallery.id}/approve`, "POST");
  await logout(page);
  await login(page, creator.email, creator.password);
  return gallery;
}

async function detail(page: Page, caseId: string) {
  return apiOk<{ status: string; severity: string; queue: string; report_count: number; actions: { id: string; type: string; reversal_action_id: string | null }[]; safe_evidence: { source_type: string; snapshot: Record<string, string> }[] }>(page, `/trust-safety/cases/${caseId}`);
}

test("Phase 13 real-stack report, appeal, consent, and critical-containment journeys", async ({ page }) => {
  const stamp = String(Date.now());
  const creator = await createApprovedCreator(page, stamp);

  const reportable = await publishGallery(page, creator, `Reported ${stamp}`);
  await logout(page);
  await login(page, admin.email, admin.password);
  const surface = await apiOk<{ id: string }>(page, "/featuring/admin/surfaces", "POST", { kind: "discover_home_hero", cancellation_cutoff_seconds: 0 });
  const slot = await apiOk<{ id: string }>(page, "/featuring/admin/slots", "POST", { surface_id: surface.id, slot_key: `phase13-${stamp}`, position: 0, capacity: 1 });
  await apiOk(page, "/featuring/admin/prices", "POST", { slot_id: slot.id, target_type: "gallery", duration_seconds: 120, amount_minor: 900, currency: "EUR" });
  await logout(page);
  await login(page, creator.email, creator.password);
  const booking = await apiOk<{ id: string }>(page, "/featuring/bookings", "POST", { slot_id: slot.id, target_type: "gallery", target_id: reportable.id, starts_at: new Date(Date.now() + 1000).toISOString(), duration_seconds: 120 });
  const payment = await apiOk<{ payment_attempt_id: string }>(page, `/featuring/bookings/${booking.id}/payment`, "POST");
  await apiOk(page, `/payments/development/${payment.payment_attempt_id}/complete`, "POST");
  await logout(page);
  await login(page, admin.email, admin.password);
  await expect.poll(async () => {
    await apiOk(page, "/featuring/admin/reconcile", "POST");
    return (await apiOk<{ id: string; status: string }[]>(page, "/featuring/admin/bookings")).find((item) => item.id === booking.id)?.status;
  }).toBe("active");
  await logout(page);
  await register(page, `phase13-reporter-${stamp}@example.com`, "phase13-reporter-password");
  const report = await apiOk<{ case_id: string }>(page, "/trust-safety/reports", "POST", { target_type: "media", target_id: reportable.id, reason: "non_consensual_content", details: "Exact report context for the published gallery." });
  await logout(page);
  await login(page, moderator.email, moderator.password);
  const moderatorAccount = await apiOk<{ id: string }>(page, "/me");
  await page.goto("/moderation");
  await page.getByRole("button", { name: report.case_id }).click();
  await page.getByLabel("Assign moderator ID").fill(moderatorAccount.id);
  await page.getByRole("button", { name: "Assign" }).click();
  await page.getByLabel("Enforcement target ID").fill(reportable.id);
  await page.getByLabel("Enforcement reason").fill("Evidence supports temporary containment.");
  await page.getByRole("button", { name: "Apply enforcement" }).click();
  await expect(page.getByText("temporary_containment")).toBeVisible();
  const reportCase = (await apiOk<{ id: string; public_id: string; assigned_moderator_id: string | null }[]>(page, "/trust-safety/cases")).find((item) => item.public_id === report.case_id)!;
  expect(reportCase.assigned_moderator_id).toBe(moderatorAccount.id);
  const moderated = await detail(page, reportCase.id);
  expect(moderated.actions.some((item) => item.type === "temporary_containment")).toBe(true);
  expect(moderated.safe_evidence.some((item) => item.source_type === "report_context")).toBe(true);
  expect((await api(page, `/content/public/${reportable.id}`)).status).toBe(404);
  expect((await apiOk<{ items: { id: string }[] }>(page, `/discovery/search?q=${encodeURIComponent(reportable.title)}`)).items.some((item) => item.id === reportable.id)).toBe(false);
  await logout(page);
  await login(page, admin.email, admin.password);
  await expect.poll(async () => {
    await apiOk(page, "/featuring/admin/reconcile", "POST");
    return (await apiOk<{ id: string; status: string }[]>(page, "/featuring/admin/bookings")).find((item) => item.id === booking.id)?.status;
  }).toMatch(/suspended|refunded/);

  await logout(page);
  await login(page, creator.email, creator.password);
  await page.goto("/appeals");
  const action = moderated.actions.find((item) => item.type === "temporary_containment")!;
  await page.getByLabel("Enforcement action ID").fill(action.id);
  await page.getByLabel("Appeal reason").fill("The report does not support the action.");
  await page.getByRole("button", { name: "Submit appeal" }).click();
  await expect(page.getByRole("status")).toContainText("submitted");
  const appeal = await apiOk<{ id: string }>(page, `/trust-safety/actions/${action.id}/appeals`, "POST", { reason: "The report does not support the action." });
  await logout(page);
  await login(page, reviewer.email, reviewer.password);
  await page.goto("/moderation/appeals");
  await page.getByLabel("Appeal ID").fill(appeal.id);
  await page.getByLabel("Outcome").selectOption("overturned");
  await page.getByLabel("Decision reason").fill("Independent reviewer overturned containment.");
  await page.getByRole("button", { name: "Record decision" }).click();
  await expect(page.getByRole("status")).toContainText("overturned");
  const restored = await detail(page, reportCase.id);
  expect(restored.actions.map((item) => item.type)).toEqual(expect.arrayContaining(["temporary_containment", "content_restore"]));

  await logout(page);
  await login(page, creator.email, creator.password);
  const consented = await publishGallery(page, creator, `Consent ${stamp}`, true, false);
  const separatelyConsented = await publishGallery(page, creator, `Consent scope ${stamp}`, true, false);
  await page.goto("/creator-studio/consent");
  await page.getByLabel("Creator ID").fill(creator.id);
  await page.getByLabel("Linked content ID").fill(consented.id);
  await page.getByLabel("Participant reference").fill("private-performer-reference");
  await page.getByRole("button", { name: "Submit release" }).click();
  await expect(page.getByText("co_performer_release · pending")).toBeVisible();
  expect((await api(page, `/content/public/${consented.id}`)).status).toBe(404);
  const releases = await apiOk<{ id: string; status: string }[]>(page, `/trust-safety/creators/${creator.id}/consent-releases`);
  const release = releases.find((item) => item.status === "pending")!;
  const separateRelease = await apiOk<{ id: string; status: string }>(page, `/trust-safety/creators/${creator.id}/consent-releases`, "POST", {
    release_type: "co_performer_release",
    participant_reference: "separate-private-performer-reference",
    content_ids: [separatelyConsented.id],
  });
  expect(separateRelease.status).toBe("pending");
  await logout(page);
  await login(page, moderator.email, moderator.password);
  await page.goto("/moderation/consent");
  await page.getByLabel("Release ID").fill(release.id);
  await page.getByRole("button", { name: "Record review" }).click();
  await expect(page.getByRole("status")).toContainText("verified");
  await page.getByLabel("Release ID").fill(separateRelease.id);
  await page.getByRole("button", { name: "Record review" }).click();
  await expect(page.getByRole("status")).toContainText("verified");
  await apiOk(page, `/admin/content-review/${consented.id}/approve`, "POST");
  await apiOk(page, `/admin/content-review/${separatelyConsented.id}/approve`, "POST");
  await expect.poll(async () => (await api(page, `/content/public/${consented.id}`)).status).toBe(200);
  await expect.poll(async () => (await api(page, `/content/public/${separatelyConsented.id}`)).status).toBe(200);
  await logout(page);
  await login(page, creator.email, creator.password);
  const consentBooking = await apiOk<{ id: string }>(page, "/featuring/bookings", "POST", { slot_id: slot.id, target_type: "gallery", target_id: consented.id, starts_at: new Date(Date.now() + 1000).toISOString(), duration_seconds: 120 });
  const consentPayment = await apiOk<{ payment_attempt_id: string }>(page, `/featuring/bookings/${consentBooking.id}/payment`, "POST");
  await apiOk(page, `/payments/development/${consentPayment.payment_attempt_id}/complete`, "POST");
  await logout(page);
  await login(page, admin.email, admin.password);
  await expect.poll(async () => {
    await apiOk(page, "/featuring/admin/reconcile", "POST");
    return (await apiOk<{ id: string; status: string }[]>(page, "/featuring/admin/bookings")).find((item) => item.id === consentBooking.id)?.status;
  }).toBe("active");
  await logout(page);
  await login(page, creator.email, creator.password);
  await page.goto("/creator-studio/consent");
  await page.getByLabel("Creator ID").fill(creator.id);
  await page.getByRole("button", { name: "Load releases" }).click();
  await page.getByRole("button", { name: `Revoke release ${release.id}` }).click();
  await expect.poll(async () => (await api(page, `/content/public/${consented.id}`)).status).toBe(404);
  expect((await api(page, `/content/public/${separatelyConsented.id}`)).status).toBe(200);
  expect((await apiOk<{ items: { id: string }[] }>(page, `/discovery/search?q=${encodeURIComponent(consented.title)}`)).items.some((item) => item.id === consented.id)).toBe(false);
  await logout(page);
  await login(page, admin.email, admin.password);
  await expect.poll(async () => {
    await apiOk(page, "/featuring/admin/reconcile", "POST");
    return (await apiOk<{ id: string; status: string }[]>(page, "/featuring/admin/bookings")).find((item) => item.id === consentBooking.id)?.status;
  }).toMatch(/suspended|refunded/);
  await logout(page);
  await login(page, creator.email, creator.password);
  const replacement = await apiOk<{ id: string; status: string }>(page, `/trust-safety/creators/${creator.id}/consent-releases`, "POST", {
    release_type: "co_performer_release",
    participant_reference: "private-performer-reference",
    content_ids: [consented.id],
    supersedes_release_id: release.id,
  });
  expect(replacement.status).toBe("pending");
  await logout(page);
  await login(page, moderator.email, moderator.password);
  await page.goto("/moderation/consent");
  await page.getByLabel("Release ID").fill(replacement.id);
  await page.getByRole("button", { name: "Record review" }).click();
  await expect(page.getByRole("status")).toContainText("verified");
  await expect.poll(async () => (await api(page, `/content/public/${consented.id}`)).status).toBe(200);
  expect((await api(page, `/content/public/${separatelyConsented.id}`)).status).toBe(200);

  await logout(page);
  await login(page, creator.email, creator.password);
  const critical = await publishGallery(page, creator, `Critical ${stamp}`);
  await register(page, `phase13-critical-${stamp}@example.com`, "phase13-critical-password");
  const criticalReport = await apiOk<{ case_id: string }>(page, "/trust-safety/reports", "POST", { target_type: "media", target_id: critical.id, reason: "underage_concern", details: "Credible evidence requires urgent containment." });
  await apiOk(page, "/trust-safety/reports", "POST", { target_type: "media", target_id: critical.id, reason: "underage_concern", details: "Repeated credible evidence." });
  await logout(page);
  await login(page, moderator.email, moderator.password);
  const criticalCase = await expect.poll(async () => {
    const cases = await apiOk<{ id: string; public_id: string; severity: string }[]>(page, "/trust-safety/cases");
    return cases.find((item) => item.public_id === criticalReport.case_id);
  }).toBeTruthy();
  void criticalCase;
  const cases = await apiOk<{ id: string; public_id: string; severity: string }[]>(page, "/trust-safety/cases");
  const criticalCaseRow = cases.find((item) => item.public_id === criticalReport.case_id)!;
  expect(criticalCaseRow.severity).toBe("critical");
  const criticalDetail = await detail(page, criticalCaseRow.id);
  expect(criticalDetail.actions.filter((item) => item.type === "temporary_containment")).toHaveLength(1);
  expect((await api(page, `/content/public/${critical.id}`)).status).toBe(404);
  await logout(page);
  await login(page, creator.email, creator.password);
  const criticalAction = criticalDetail.actions.find((item) => item.type === "temporary_containment")!;
  const criticalAppeal = await apiOk<{ id: string }>(page, `/trust-safety/actions/${criticalAction.id}/appeals`, "POST", { reason: "Containment can be safely overturned after review." });
  await logout(page);
  await login(page, moderator.email, moderator.password);
  await page.goto("/moderation/appeals");
  await page.getByLabel("Appeal ID").fill(criticalAppeal.id);
  await page.getByLabel("Outcome").selectOption("overturned");
  await page.getByLabel("Decision reason").fill("Independent review permits restoration.");
  await page.getByRole("button", { name: "Record decision" }).click();
  await expect(page.getByRole("status")).toContainText("overturned");
  await logout(page);
  await login(page, admin.email, admin.password);
  await apiOk(page, `/admin/content-review/${critical.id}/approve`, "POST");
  await expect.poll(async () => (await api(page, `/content/public/${critical.id}`)).status).toBe(200);
  const criticalRestored = await detail(page, criticalCaseRow.id);
  expect(criticalRestored.actions.map((item) => item.type)).toEqual(expect.arrayContaining(["temporary_containment", "content_restore"]));
});
