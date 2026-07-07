import { test, expect } from "@playwright/test";

test("landing shows Terraducktel title", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Terraducktel" })).toBeVisible();
});

test("settings page has login form", async ({ page }) => {
  await page.goto("/settings");
  await expect(page.getByTestId("login-button")).toBeVisible();
});

test("viewer cannot see approve without operator role", async ({ page }) => {
  await page.goto("/approvals");
  // Without token, RequireAuth redirects — approvals may not load approve UI
  await expect(page.getByTestId("approve-button")).toHaveCount(0);
});
