import { expect, type Page } from "@playwright/test";

const apiBase = process.env.E2E_API_URL ?? "http://127.0.0.1:38180";

export async function expectAuthenticatedAs(page: Page, email: string) {
  await expect(page).toHaveURL(/\/account(?:[?#].*)?$/, { timeout: 15_000 });
  await expect(
    page.getByRole("main").getByRole("heading", { name: "Your FanBackstage" }),
  ).toBeVisible();
  await expect.poll(async () => page.evaluate(async ({ apiBase }) => {
    try {
      const response = await fetch(`${apiBase}/api/v1/me`, { credentials: "include" });
      if (!response.ok) return null;
      return ((await response.json()) as { email?: string }).email ?? null;
    } catch {
      return null;
    }
  }, { apiBase }), { timeout: 15_000 }).toBe(email);
}
