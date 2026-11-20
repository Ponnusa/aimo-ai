import { expect, test } from "@playwright/test";

test("dev login, review a pending approval, approve it", async ({ page }) => {
  await page.goto("/login");

  await page.getByLabel("Organization id").fill("org_fictional_bistro_01");
  await page.getByLabel("Your subject id").fill("dev-approver-1");
  await page.getByLabel("Email").fill("dev-approver-1@example.com");
  await page.getByLabel("Display name").fill("Dev Approver");
  await page.getByRole("button", { name: "Continue (dev login)" }).click();

  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByText("Pending approvals")).toBeVisible();

  await page.getByRole("link", { name: "Pending approvals" }).click();
  await expect(page).toHaveURL(/\/approvals/);
  await page.getByRole("link", { name: "Review" }).click();

  await expect(page).toHaveURL(/\/approvals\/1/);
  await expect(page.getByText("txn_e2e_01")).toBeVisible();

  await page.getByRole("button", { name: "Approve" }).click();

  await expect(page).toHaveURL(/\/approvals\/1/);
  await expect(page.getByText("APPROVED")).toBeVisible();
});
