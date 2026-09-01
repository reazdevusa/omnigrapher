import { test, expect, Page } from "@playwright/test";

// End-to-end smoke test for the AEO Studio dashboard.
// Assumes the Next.js dev server and the backend API are running.
// The backend must have an AEO Studio API key configured or allow
// unauthenticated access for this test to reach the dashboard.

const AEO_API_KEY = process.env.AEO_TEST_API_KEY || "";
const AEO_BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

test.describe("AEO Studio dashboard", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/dashboard/aeo-studio");
  });

  test("dashboard renders with input form and tabs", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "AEO Studio" })).toBeVisible();
    await expect(page.getByLabel("Business Name")).toBeVisible();
    await expect(page.getByLabel("Industry / Schema Type")).toBeVisible();
    await expect(page.getByLabel("Location")).toBeVisible();
    await expect(page.getByLabel("Seed Keywords")).toBeVisible();
    await expect(page.getByRole("button", { name: "Generate AEO Pages" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Bulk CSV Upload" })).toBeVisible();
  });

  test("end-to-end generation shows progress and preview", async ({ page }) => {
    test.setTimeout(120_000);
    test.skip(!AEO_API_KEY, "AEO_TEST_API_KEY is not set");

    page.on("console", (msg) => {
      if (msg.type() === "error") {
        console.error("[browser console error]", msg.text());
      } else {
        console.log(`[browser console ${msg.type()}]`, msg.text());
      }
    });

    await page.getByPlaceholder("AEO API Key").fill(AEO_API_KEY);
    await page.getByLabel("Business Name").fill("Test Roofing LLC");
    await page.getByLabel("Location").fill("Austin, TX");
    await page.getByLabel("Industry / Schema Type").fill("RoofingContractor");

    await page.getByRole("button", { name: "Generate AEO Pages" }).click();

    // Wait for the progress tab to become active and show agent logs.
    await expect(page.getByRole("tab", { name: "Live Progress" })).toHaveAttribute(
      "data-state",
      "active",
      { timeout: 10_000 }
    );

    // Wait up to 60s for the job to complete.
    const doneBadge = page.locator('[data-testid="aeo-status-badge"]').filter({ hasText: "completed" });
    await doneBadge.waitFor({ timeout: 60_000 });

    // Preview tab should become available and eventually auto-selected.
    await expect(page.getByRole("tab", { name: "Live Preview" })).toBeEnabled();
    await expect(page.getByRole("tab", { name: "Live Preview" })).toHaveAttribute("data-state", "active", { timeout: 15_000 });
  });
});
