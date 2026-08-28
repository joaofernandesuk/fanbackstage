import { expect, test } from "@playwright/test";

import { completeRegistrationCompliance, expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

test("complete real authentication lifecycle", async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`;
  const old = "original-password-123";
  const fresh = "replacement-password-123";
  await page.goto("/register?next=%2Faccount%3Ffrom%3Dregistration");
  await completeRegistrationCompliance(page);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(old);
  const adultConfirmation = page.getByRole("checkbox", {
    name: "I confirm I am at least 18 years old.",
  });
  await expect(adultConfirmation).not.toBeChecked();
  await adultConfirmation.check();
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Verify your email" })).toBeVisible();
  await expect(page).toHaveURL(/\/verify-email\?next=%2Faccount%3Ffrom%3Dregistration$/);
  await page.goto("/login?next=%2Faccount%3Ffrom%3Dregistration");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(old);
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await expect(page.getByText("Verify your email address before logging in.")).toBeVisible();
  await page.getByRole("button", { name: "Resend verification email" }).click();
  await expect(page.getByText("If the account needs verification, a new link has been sent.")).toBeVisible();
  const verify = await securityLink(email, "/verify-email");
  await page.goto(verify);
  await page.getByRole("button", { name: "Verify email" }).click();
  await expect(page.getByText("Email verified")).toBeVisible();
  const loginHandoff = page.getByRole("link", { name: "Log in to continue" });
  await expect(loginHandoff).toHaveAttribute(
    "href",
    "/login?next=%2Faccount%3Ffrom%3Dregistration",
  );
  await page.getByRole("button", { name: "Verify email" }).click();
  await expect(page.getByText("Token is invalid or expired")).toBeVisible();
  await loginHandoff.click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(old);
  await page.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);
  await page.getByRole("button", { name: "Log out" }).click();
  await page.goto("/account");
  await expect(page.getByText("Authentication required")).toBeVisible();
  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  const reset = await securityLink(email, "/reset-password");
  await page.goto(reset);
  await page.getByLabel("New password").fill(fresh);
  await page.getByRole("button", { name: "Reset password" }).click();
  await expect(page.getByText("Password reset")).toBeVisible();
  await page.getByRole("button", { name: "Reset password" }).click();
  await expect(page.getByText("Token is invalid or expired")).toBeVisible();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(old);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByText("The email address or password is incorrect.")).toBeVisible();
  await page.getByLabel("Password", { exact: true }).fill(fresh);
  await page.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);
});

test("public login gate uses the global dialog and returns to the safe page", async ({ page }) => {
  await page.goto("/discover?sort=trending");
  await page.getByRole("link", { name: "Log in" }).click();

  const dialog = page.getByRole("dialog", { name: "Log in to FanBackstage" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Email address").fill("phase2-e2e-admin@example.com");
  await dialog.getByLabel("Password", { exact: true }).fill(
    "phase2-e2e-admin-password",
  );
  await dialog.locator("form").getByRole("button", { name: "Log in", exact: true }).click();

  await expect(page).toHaveURL(/\/discover\?sort=trending$/);
  await expect(page.getByLabel("Open account menu", { exact: true })).toBeVisible();
});

test("global dialog preserves a deep link when login switches to join", async ({ page }) => {
  const email = `modal-join-${Date.now()}@example.com`;
  await page.goto("/discover?sort=latest");
  await page.getByRole("link", { name: "Log in" }).click();

  const loginDialog = page.getByRole("dialog", { name: "Log in to FanBackstage" });
  await loginDialog.getByRole("button", { name: "Join", exact: true }).click();
  const joinDialog = page.getByRole("dialog", { name: "Join FanBackstage" });
  await expect(joinDialog).toBeVisible();
  await joinDialog.getByLabel("Country or jurisdiction").selectOption("PT");
  await joinDialog.getByRole("button", { name: "Verify age to continue" }).click();
  await expect(page).toHaveURL(/\/register\?next=%2Fdiscover%3Fsort%3Dlatest$/);
  await completeRegistrationCompliance(page, false);
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill("modal-join-password-123");
  const adultConfirmation = page.getByRole("checkbox", {
    name: "I confirm I am at least 18 years old.",
  });
  await expect(adultConfirmation).not.toBeChecked();
  await adultConfirmation.check();
  await page.locator("form").getByRole("button", { name: "Create account", exact: true }).click();

  await expect(page).toHaveURL(/\/verify-email\?next=%2Fdiscover%3Fsort%3Dlatest$/);
});

test("logged-out discovery Subscribe opens canonical login and returns to the creator profile", async ({ page }) => {
  await page.goto("/discover");
  await page.getByRole("button", { name: "Verify age" }).click();
  const subscribe = page.getByRole("link", { name: /^Subscribe(?:\s|$)/ }).first();
  await expect(subscribe).toBeVisible();
  await expect(subscribe).toHaveAttribute("aria-haspopup", "dialog");
  await subscribe.click();

  const dialog = page.getByRole("dialog", { name: "Log in to FanBackstage" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Email address").fill("phase2-e2e-admin@example.com");
  await dialog.getByLabel("Password", { exact: true }).fill(
    "phase2-e2e-admin-password",
  );
  await dialog.locator("form").getByRole("button", { name: "Log in", exact: true }).click();

  await expect(page).toHaveURL(/\/creator\/[a-z0-9-]+$/);
  await expect(page.getByRole("button", { name: "View membership options" })).toBeVisible();
});
