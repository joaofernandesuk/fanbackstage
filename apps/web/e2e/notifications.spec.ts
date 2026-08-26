import { expect, test } from "@playwright/test";

import { expectAuthenticatedAs } from "./auth-helpers";
import { securityLink } from "./mailpit";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";

async function login(page: import("@playwright/test").Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await expectAuthenticatedAs(page, email);
}

test("notification center is recipient-scoped and reflects read state", async ({ browser, page }) => {
  const stamp = Date.now();
  const email = `notifications-${stamp}@example.com`;
  const password = "notifications-password-123";
  const replacement = "notifications-replacement-123";

  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.goto(await securityLink(email, "/verify-email"));
  await page.getByRole("button", { name: "Verify email" }).click();
  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await page.goto(await securityLink(email, "/reset-password"));
  await page.getByLabel("New password").fill(replacement);
  await page.getByRole("button", { name: "Reset password" }).click();
  await login(page, email, replacement);

  await page.goto("/notifications");
  await expect(page.getByText("1 unread")).toBeVisible();
  await expect(page.getByText("Your FanBackstage password was changed.")).toBeVisible();
  const notificationId = await page.evaluate(async ({ apiBase }) => {
    const response = await fetch(`${apiBase}/api/v1/notifications`, { credentials: "include" });
    const body = await response.json() as { items: { id: string }[] };
    return body.items[0].id;
  }, { apiBase });
  await page.getByRole("button", { name: "Mark read" }).click();
  await expect(page.getByText("0 unread")).toBeVisible();

  const other = await browser.newContext();
  const otherPage = await other.newPage();
  const otherEmail = `notifications-other-${stamp}@example.com`;
  await otherPage.goto("/register");
  await otherPage.getByLabel("Email").fill(otherEmail);
  await otherPage.getByRole("textbox", { name: /^Password\b/ }).fill(password);
  await otherPage.getByRole("button", { name: "Create account" }).click();
  await otherPage.goto(await securityLink(otherEmail, "/verify-email"));
  await otherPage.getByRole("button", { name: "Verify email" }).click();
  await login(otherPage, otherEmail, password);
  const forbidden = await otherPage.evaluate(async ({ apiBase, notificationId }) => {
    const response = await fetch(`${apiBase}/api/v1/notifications/${notificationId}/read`, {
      method: "POST", credentials: "include",
    });
    return response.status;
  }, { apiBase, notificationId });
  expect(forbidden).toBe(404);
  await other.close();
});
