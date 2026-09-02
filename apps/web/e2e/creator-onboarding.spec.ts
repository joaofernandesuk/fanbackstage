import { expect, test } from "@playwright/test";

import { completeRegistrationCompliance, expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

test("creator can register, verify, and submit an identity application", async ({ page }) => {
  const email = `creator-${Date.now()}@example.com`;
  const password = "creator-password-123";
  await page.goto("/register");
  await completeRegistrationCompliance(page);
  const registrationForm = page.getByRole("main").locator("form");
  await registrationForm.getByLabel("Email").fill(email);
  await registrationForm.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await registrationForm.getByRole("checkbox", { name: /I confirm I am at least 18/ }).check();
  await registrationForm.getByRole("button", { name: "Create account" }).click();
  const verificationLink = await securityLink(email, "/verify-email");
  await page.goto(verificationLink);
  await page.getByRole("main").getByRole("button", { name: "Verify email" }).click();
  await page.goto("/login");
  const loginForm = page.getByRole("main").locator("form");
  await loginForm.getByLabel("Email").fill(email);
  await loginForm.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await loginForm.getByRole("button", { name: "Log in" }).click();
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByRole("textbox", { name: /^Your @handle/ }).fill(`creator${Date.now()}`);
  await page.getByLabel("Display name").fill("Creator Example");
  await page.getByLabel("Bio").fill("A public creator profile.");
  await page.getByLabel("Country or territory").fill("Portugal");
  await page.getByRole("option", { name: "Portugal PT" }).click();
  await page.getByLabel("Region", { exact: true }).fill("Lisbon");
  await page.getByLabel("City", { exact: true }).fill("Lisbon");
  await expect(page.getByLabel("Timezone", { exact: true })).toHaveValue("Europe/Lisbon");
  await page.getByLabel(/Show my configured city/).check();
  const interest = page.getByLabel("Available creator interests").getByRole("button", { name: /Video & behind the scenes/ });
  const language = page.getByLabel("Available creator languages").getByRole("button", { name: /English EN/ });
  await interest.click();
  await language.click();
  await page.getByLabel("Label", { exact: true }).fill("Portfolio");
  await page.getByLabel("URL", { exact: true }).fill("https://creator.example/portfolio");
  await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/api/v1/creators/me") && response.request().method() === "PATCH" && response.ok()),
    page.getByRole("button", { name: "Save profile" }).click(),
  ]);
  await page.reload();
  await expect(page.getByLabel("Country or territory")).toHaveValue("Portugal");
  await expect(page.getByLabel("Available creator interests").getByRole("button", { name: /Video & behind the scenes/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Available creator languages").getByRole("button", { name: /English EN/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Label", { exact: true })).toHaveValue("Portfolio");
  await expect(page.getByLabel("URL", { exact: true })).toHaveValue("https://creator.example/portfolio");
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect(page.getByText("pending verification", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^(Complete development verification|Complete identity check)$/ }).click();
  await expect(page.getByText("pending review", { exact: true })).toBeVisible();
});

test("creator onboarding hides the development shortcut when the API capability is disabled", async ({ page }) => {
  const email = "phase2-e2e-admin@example.com";
  await page.goto("/login");
  const loginForm = page.getByRole("main").locator("form");
  await loginForm.getByLabel("Email").fill(email);
  await loginForm.getByRole("textbox", { name: /^Password\b/ }).fill("phase2-e2e-admin-password");
  await loginForm.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);

  await page.route("**/api/v1/creators/me", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      json: {
        username: "pending-creator",
        display_name: "Pending Creator",
        bio: null,
        country_code: null,
        region: null,
        city: null,
        show_location: false,
        timezone: null,
        status: "pending_verification",
        is_public: false,
        verification_status: "not_started",
        adult_verified: false,
        creator_compliance: {
          jurisdiction: "PT",
          policy_version: 2,
          verification_status: null,
          verification_expires_at: null,
          identity_required: true,
          identity_allowed: false,
          age_required: true,
          age_allowed: false,
          public_allowed: false,
          payout_kyc_required: true,
          payout_kyc_satisfied: false,
          payout_allowed: false,
          code: "CREATOR_IDENTITY_VERIFICATION_REQUIRED",
          reason: "Current creator identity verification is required",
          payout_code: "PAYOUT_KYC_REQUIRED",
        },
        performer_consent_issue_count: 0,
        creator_compliance_action_required: true,
        rejection_reason: null,
        languages: [],
        categories: [],
        social_links: [],
        available_languages: [],
        available_categories: [],
        development_verification_available: false,
      },
      status: 200,
    });
  });

  await page.goto("/creator-onboarding");
  await expect(page.getByRole("heading", { name: "Identity verification is pending" })).toBeVisible();
  await expect(page.getByText(/Development verification is disabled here/)).toBeVisible();
  await expect(page.getByRole("button", { name: /^(Complete development verification|Complete identity check)$/ })).toHaveCount(0);
});
