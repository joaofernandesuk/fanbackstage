import { expect, test } from "@playwright/test";

import { securityLink } from "./mailpit";

test("creator can register, verify, and submit an identity application", async ({ page }) => {
  const email = `creator-${Date.now()}@example.com`;
  const password = "creator-password-123";
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  const verificationLink = await securityLink(email, "/verify-email");
  await page.goto(verificationLink);
  await page.getByRole("button", { name: "Verify email" }).click();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByRole("link", { name: "Become a creator" }).click();
  await page.getByLabel("Username").fill(`creator${Date.now()}`);
  await page.getByLabel("Display name").fill("Creator Example");
  await page.getByLabel("Bio").fill("A public creator profile.");
  await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/api/v1/creators/me") && response.request().method() === "PATCH" && response.ok()),
    page.getByRole("button", { name: "Save profile" }).click(),
  ]);
  await page.getByRole("button", { name: "Submit application" }).click();
  await expect(page.getByText("pending verification")).toBeVisible();
  await page.getByRole("button", { name: "Complete development verification" }).click();
  await expect(page.getByText("pending review")).toBeVisible();
});
