import { expect, test, type Page } from "@playwright/test";

import { expectAuthenticatedAs } from "./auth-helpers";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";
const adminEmail = "phase2-e2e-admin@example.com";
const adminPassword = "phase2-e2e-admin-password";

type ApiResult<T> = { status: number; body: T };
type PolicyRules = Record<string, boolean | number | string | null>;

async function api<T>(
  page: Page,
  path: string,
  method = "GET",
  body?: unknown,
): Promise<ApiResult<T>> {
  return page.evaluate(async ({ apiBase, path, method, body }) => {
    const response = await fetch(`${apiBase}/api/v1${path}`, {
      method,
      credentials: "include",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    return {
      status: response.status,
      body: (text ? JSON.parse(text) : null) as T,
    };
  }, { apiBase, path, method, body });
}

async function loginAdmin(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email address").fill(adminEmail);
  await page.getByLabel("Password", { exact: true }).fill(adminPassword);
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await expectAuthenticatedAs(page, adminEmail);
}

async function publishPtPolicy(
  page: Page,
  updates: PolicyRules,
  reason: string,
) {
  const templates = await api<{
    items: { id: string; key: string }[];
  }>(page, "/admin/compliance/templates?page=1&page_size=100");
  expect(templates.status).toBe(200);
  const template = templates.body.items.find((item) => item.key === "e2e-test-baseline");
  expect(template).toBeTruthy();

  const revisions = await api<{
    id: string;
    rules: PolicyRules;
  }[]>(page, `/admin/compliance/templates/${template!.id}/revisions`);
  expect(revisions.status).toBe(200);
  expect(revisions.body.length).toBeGreaterThan(0);
  const effectiveFrom = new Date(Date.now() - 1_000).toISOString();
  const revision = await api<{ id: string; version: number }>(
    page,
    `/admin/compliance/templates/${template!.id}/revisions`,
    "POST",
    {
      rules: { ...revisions.body[0].rules, ...updates },
      status: "active",
      effective_from: effectiveFrom,
      effective_until: null,
      reviewed: true,
      is_demo: true,
      change_reason: reason,
      confirmation: "CONFIRM_COMPLIANCE_CHANGE",
    },
  );
  expect(revision.status).toBe(200);
  const jurisdiction = await api<{ id: string; version: number }>(
    page,
    "/admin/compliance/jurisdictions/PT/revisions",
    "POST",
    {
      template_revision_id: revision.body.id,
      overrides: {},
      status: "active",
      effective_from: effectiveFrom,
      effective_until: null,
      reviewed: true,
      is_demo: true,
      change_reason: reason,
      confirmation: "CONFIRM_COMPLIANCE_CHANGE",
    },
  );
  expect(jurisdiction.status).toBe(200);
  return revision.body;
}

test.describe.serial("real-stack compliance lifecycle", () => {
  test("B: an authenticated fan completes provider verification", async ({ page }) => {
    await loginAdmin(page);
    const started = await api<{
      authorization_url: string;
      status: string;
      required_assurance_level: string;
    }>(page, "/compliance/age-verification/start", "POST", {
      country_code: "PT",
      return_path: "/account",
    });
    expect(started.status).toBe(200);
    expect(started.body.status).toBe("pending");
    await page.goto(started.body.authorization_url);
    await expect(page).toHaveURL(/\/account$/);

    const status = await api<{
      fan_age_verification: {
        status: string;
        achieved_assurance_level: string;
      } | null;
      adult_media_decision: { allowed: boolean; code: string };
    }>(page, "/compliance/age-verification/status");
    expect(status.status).toBe(200);
    expect(status.body.fan_age_verification).toMatchObject({
      status: "verified",
      achieved_assurance_level: "medium",
    });
    expect(status.body.adult_media_decision).toMatchObject({
      allowed: true,
      code: "ALLOWED",
    });
  });

  test("D: a stronger reviewed policy requires re-verification", async ({ page }) => {
    await loginAdmin(page);
    await publishPtPolicy(
      page,
      { required_assurance_level: "high" },
      "E2E require high assurance and prove re-verification",
    );
    try {
      const status = await api<{
        fan_age_verification: { status: string } | null;
        adult_media_decision: { allowed: boolean; code: string; action: string };
      }>(page, "/compliance/age-verification/status");
      expect(status.body.fan_age_verification?.status).toBe("verified");
      expect(status.body.adult_media_decision).toMatchObject({
        allowed: false,
        code: "AGE_ASSURANCE_INSUFFICIENT",
        action: "VERIFY_AGE",
      });
      await page.goto("/account");
      const ageCard = page.getByRole("region", { name: "Age assurance" });
      await expect(
        ageCard.getByText("Current access decision: Age Assurance Insufficient"),
      ).toBeVisible();
      await expect(ageCard.getByRole("button", { name: "Verify age" })).toBeVisible();
    } finally {
      await publishPtPolicy(
        page,
        { required_assurance_level: "self_attested" },
        "E2E restore baseline assurance after stronger-policy check",
      );
    }
  });

  test("E: a disabled country blocks runtime access and can be restored", async ({ page }) => {
    await loginAdmin(page);
    const disable = await api<{ code: string; enabled: boolean }>(
      page,
      "/admin/compliance/countries/PT/availability",
      "PUT",
      {
        enabled: false,
        change_reason: "E2E prove disabled-jurisdiction runtime containment",
        confirmation: "CONFIRM_COMPLIANCE_CHANGE",
      },
    );
    expect(disable).toMatchObject({ status: 200, body: { code: "PT", enabled: false } });
    try {
      const decision = await api<{ allowed: boolean; code: string; action: string }>(
        page,
        "/compliance/decision?feature=platform_access&adult_restricted=false",
      );
      expect(decision.body).toMatchObject({
        allowed: false,
        code: "JURISDICTION_BLOCKED",
        action: "CONTACT_SUPPORT",
      });
      const publicCreator = await api<unknown>(page, "/creators/e2e-backstage-host");
      expect(publicCreator.status).toBe(404);
    } finally {
      const restore = await api<{ code: string; enabled: boolean }>(
        page,
        "/admin/compliance/countries/PT/availability",
        "PUT",
        {
          enabled: true,
          change_reason: "E2E restore PT after disabled-jurisdiction check",
          confirmation: "CONFIRM_COMPLIANCE_CHANGE",
        },
      );
      expect(restore).toMatchObject({ status: 200, body: { code: "PT", enabled: true } });
    }
  });

  test("F: an admin policy change is audited and immediately enforced", async ({ page }) => {
    await loginAdmin(page);
    const changed = await publishPtPolicy(
      page,
      { messaging_allowed: false },
      "E2E disable messaging and prove runtime enforcement",
    );
    try {
      const decision = await api<{ allowed: boolean; code: string; action: string }>(
        page,
        "/compliance/decision?feature=messaging&adult_restricted=false",
      );
      expect(decision.body).toMatchObject({
        allowed: false,
        code: "FEATURE_UNAVAILABLE",
        action: "CONTACT_SUPPORT",
      });
      const creator = await api<{ id: string; username: string }>(
        page,
        "/creators/e2e-backstage-host",
      );
      expect(creator).toMatchObject({
        status: 200,
        body: { username: "e2e-backstage-host" },
      });
      const sendPrice = await api<unknown>(
        page,
        `/messages/creator/${creator.body.id}/send-price`,
      );
      expect(sendPrice.status).toBe(403);
      expect(sendPrice.body).toMatchObject({
        detail: {
          code: "FEATURE_UNAVAILABLE",
          action: "CONTACT_SUPPORT",
        },
      });

      const audit = await api<{
        items: { event_type: string; target_id: string }[];
        total: number;
      }>(page, "/admin/compliance/audit?search=template_revision_created&page_size=100");
      expect(audit.status).toBe(200);
      expect(audit.body.total).toBeGreaterThan(0);
      expect(audit.body.items).toContainEqual(expect.objectContaining({
        event_type: "compliance.template_revision_created",
        target_id: changed.id,
      }));
    } finally {
      await publishPtPolicy(
        page,
        { messaging_allowed: true },
        "E2E restore messaging after runtime enforcement check",
      );
    }
  });

  test("G: publishing mandatory legal v2 gates, accepts, and records history", async ({ page }) => {
    await loginAdmin(page);
    const documents = await api<{
      items: { document_id: string; slug: string; version: number }[];
    }>(page, "/admin/legal/documents?limit=100&offset=0");
    expect(documents.status).toBe(200);
    const terms = documents.body.items.find((item) => item.slug === "terms");
    expect(terms).toBeTruthy();
    const title = `E2E Test Terms v${terms!.version + 1}`;
    const draft = await api<{ version_id: string; version: number }>(
      page,
      `/admin/legal/documents/${terms!.document_id}/versions`,
      "POST",
      {
        title,
        body: [{
          type: "callout",
          text: "Automated browser-test successor only; not production legal advice.",
        }],
        effective_from: null,
        effective_until: null,
        requires_acceptance: true,
        requires_legal_review: false,
        approved_for_publication: true,
        is_demo: true,
      },
    );
    expect(draft.status).toBe(201);
    const published = await api<{ status: string; version: number }>(
      page,
      `/admin/legal/versions/${draft.body.version_id}/publish`,
      "POST",
      {
        confirmed: true,
        reason: "Publish mandatory E2E successor and verify exact reacceptance",
      },
    );
    expect(published).toMatchObject({
      status: 200,
      body: { status: "published", version: terms!.version + 1 },
    });

    const bootstrap = await api<{ email: string }>(page, "/me");
    expect(bootstrap).toMatchObject({ status: 200, body: { email: adminEmail } });
    const blocked = await api<{ detail: { code: string; action: string } }>(
      page,
      "/messages/conversations",
    );
    expect(blocked.status).toBe(428);
    expect(blocked.body.detail).toMatchObject({
      code: "LEGAL_ACCEPTANCE_REQUIRED",
      action: "ACCEPT_LEGAL",
    });

    await page.goto("/account");
    const gate = page.getByRole("dialog");
    await expect(gate.getByRole("heading", { name: "Review before continuing" })).toBeVisible();
    await expect(gate.getByText(`${title} (version ${terms!.version + 1})`)).toBeVisible();
    await gate.getByRole("checkbox", { name: `I accept ${title} (version ${terms!.version + 1}).` }).check();
    await gate.getByRole("button", { name: "Accept and continue" }).click();
    await expect(page.getByRole("heading", { name: "Your FanBackstage" })).toBeVisible();

    await page.goto("/account/legal");
    await expect(page.getByRole("heading", { name: "Acceptance history" })).toBeVisible();
    await expect(page.getByText(title)).toBeVisible();
    await expect(page.getByText(`Version ${terms!.version + 1}`, { exact: false })).toBeVisible();
    const audit = await api<{ items: { event_type: string; target_id: string }[] }>(
      page,
      "/admin/compliance/audit?search=legal&page_size=100",
    );
    expect(audit.status).toBe(200);
    expect(audit.body.items).toContainEqual(expect.objectContaining({
      event_type: "legal.version_published",
      target_id: draft.body.version_id,
    }));
  });
});
