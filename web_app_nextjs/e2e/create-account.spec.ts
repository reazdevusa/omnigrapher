import { test, expect, Page } from "@playwright/test";
import * as fs from "fs";

// Open the app and switch the auth modal into "Create Account" mode.
async function openRegister(page: Page) {
  // Stub availability endpoints so tests can reach the enabled submit state.
  await page.route("**/auth/username-available", async (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ available: true, reason: "ok" }),
    })
  );
  await page.route("**/auth/email-available", async (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ available: true, reason: "ok" }),
    })
  );
  await page.goto("/");
  // Open the Sign In modal (either the sidebar button or the hero button).
  const signIn = page.getByRole("button", { name: /^Sign In/ }).first();
  await signIn.click();
  // Switch to the register form.
  await page.getByRole("button", { name: /Need an account\? Register/ }).click();
  await expect(page.getByRole("heading", { name: "Create Account" })).toBeVisible();
}

const createBtn = (page: Page) =>
  page.getByRole("button", { name: /Create Account/ });

test.describe("Create Account form hardening", () => {
  test("submit disabled when all fields empty", async ({ page }) => {
    await openRegister(page);
    await expect(createBtn(page)).toBeDisabled();
  });

  test("invalid email shows inline error and red border", async ({ page }) => {
    await openRegister(page);
    const email = page.getByPlaceholder("you@example.com");
    await email.fill("asldkfjldsakjfaldkj");
    await email.blur();
    await expect(page.getByText("Email must contain an @")).toBeVisible();
    await expect(email).toHaveAttribute("class", /border-red-500/);
    await expect(createBtn(page)).toBeDisabled();
    fs.mkdirSync("e2e/screenshots", { recursive: true });
    await page.screenshot({ path: "e2e/screenshots/email-malformed.png" });
  });

  test("invalid phone shows country-specific inline error and red border", async ({ page }) => {
    await openRegister(page);
    const phone = page.getByLabel("Phone number");
    await phone.fill("123");
    await phone.blur();
    await expect(page.getByText(/Enter a valid .* phone number/)).toBeVisible();
    await expect(phone).toHaveAttribute("class", /border-red-500/);
    await expect(createBtn(page)).toBeDisabled();
  });

  test("weak password shows unmet requirements and blocks submit", async ({ page }) => {
    await openRegister(page);
    await page.getByPlaceholder("Enter your password", { exact: true }).fill("weak");
    // Requirements checklist is visible; several rules unmet.
    await expect(page.getByText("An uppercase letter")).toBeVisible();
    await expect(page.getByText("A digit")).toBeVisible();
    await expect(createBtn(page)).toBeDisabled();
  });

  test("mismatched confirm password shows error and blocks submit", async ({ page }) => {
    await openRegister(page);
    await page.getByPlaceholder("Enter your password", { exact: true }).fill("Password1");
    await page.getByPlaceholder("Re-enter your password").fill("Password2");
    await expect(page.getByText("Passwords do not match")).toBeVisible();
    await expect(createBtn(page)).toBeDisabled();
  });

  test("XSS attempt in display name is stripped (no tags submitted)", async ({ page }) => {
    await openRegister(page);
    let captured: any = null;
    await page.route("**/auth/register", async (route) => {
      captured = route.request().postDataJSON();
      // Short-circuit so the test does not create a real account.
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ success: true, username: "xss_user", role: "user" }),
      });
    });

    const uniq = `xss_${Date.now()}`;
    await page.getByPlaceholder("Enter your username").fill(uniq);
    await page.getByPlaceholder("you@example.com").fill(`${uniq}@example.com`);
    await page.getByLabel("Phone number").fill("2015555555");
    await page
      .getByPlaceholder("Your public display name")
      .fill("<script>alert('x')</script>Bob");
    await page.getByPlaceholder("Enter your password", { exact: true }).fill("Password1!");
    await page.getByPlaceholder("Re-enter your password").fill("Password1!");

    await expect(createBtn(page)).toBeEnabled();
    await createBtn(page).click();

    await expect.poll(() => captured).not.toBeNull();
    expect(captured.display_name || "").not.toContain("<");
    expect(captured.display_name || "").not.toContain(">");
    expect(captured.display_name).toContain("Bob");
  });

  test("password visibility toggles are independent and accessible", async ({ page }) => {
    await openRegister(page);
    const pwd = page.getByPlaceholder("Enter your password", { exact: true });
    const confirm = page.getByPlaceholder("Re-enter your password");
    await pwd.fill("Password1");
    await confirm.fill("Password1");

    // Both default to masked.
    await expect(pwd).toHaveAttribute("type", "password");
    await expect(confirm).toHaveAttribute("type", "password");

    const showButtons = page.getByRole("button", { name: "Show password" });
    // Toggle the first (password) field only.
    await showButtons.first().click();
    await expect(pwd).toHaveAttribute("type", "text");
    await expect(confirm).toHaveAttribute("type", "password"); // unaffected

    // Value must be preserved after toggling.
    await expect(pwd).toHaveValue("Password1");

    // aria-label flips to "Hide password" for the toggled field.
    await expect(page.getByRole("button", { name: "Hide password" })).toHaveCount(1);
  });

  test("valid happy-path signup submits correct payload", async ({ page }) => {
    await openRegister(page);
    let captured: any = null;
    await page.route("**/auth/register", async (route) => {
      captured = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ success: true, username: captured.username, role: "user" }),
      });
    });
    // Stub the follow-up login so the flow completes without a real backend.
    await page.route("**/auth/login", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          access_token: "t",
          refresh_token: "r",
          username: "happy_user",
          role: "user",
        }),
      });
    });

    const uniq = `happy_${Date.now()}`;
    await page.getByPlaceholder("Enter your username").fill(uniq);
    await page.getByPlaceholder("you@example.com").fill(`${uniq}@example.com`);
    await page.getByLabel("Phone number").fill("2015555555");
    await page.getByPlaceholder("Enter your password", { exact: true }).fill("Password1!");
    await page.getByPlaceholder("Re-enter your password").fill("Password1!");

    await expect(createBtn(page)).toBeEnabled();
    await createBtn(page).click();

    await expect.poll(() => captured).not.toBeNull();
    expect(captured.username).toBe(uniq);
    expect(captured.email).toBe(`${uniq}@example.com`);
    expect(captured.password).toBe("Password1!");
    expect(captured.confirm_password).toBe("Password1!");
  });
});

