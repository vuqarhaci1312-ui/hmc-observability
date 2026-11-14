import { expect, test } from "@playwright/test";

test("login screen is available", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByText("HMC Observability")).toBeVisible();
  await expect(page.getByRole("button", { name: "Daxil ol" })).toBeVisible();
});
