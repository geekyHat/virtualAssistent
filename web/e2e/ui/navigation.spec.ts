import { defaultIdentity, expect, test } from "../fixtures";

/**
 * Prove browser di navigazione (B-08.1): refresh su URL profondi, 404
 * recuperabile, selettore lingua IT/EN, focus post-navigazione e menu
 * mobile accessibile. Le rotte API sono intercettate: nessun backend.
 */

test.describe("Navigazione — route protette e refresh", () => {
  test("refresh su /conversations e /settings con sessione attiva", async ({ page, session }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await page.goto("/conversations");
    await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();

    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Impostazioni" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { name: "Impostazioni" })).toBeVisible();
  });

  test("route protette: signed-out reindirizzato a /login", async ({ page, session }) => {
    session.set({ me: { kind: "unauthenticated" } });
    await page.goto("/conversations");
    await expect(page).toHaveURL(/\/login$/);
  });
});

test.describe("Navigazione — 404", () => {
  test("404 recuperabile su percorso ignoto con link a casa", async ({ page, session }) => {
    void session;
    await page.goto("/percorso-che-non-esiste");
    await expect(page.getByRole("heading", { name: "Pagina non trovata" })).toBeVisible();
    await page.getByRole("link", { name: "Torna alla home" }).click();
    await expect(page).toHaveURL(/\/$/);
  });
});

test.describe("Navigazione — lingua", () => {
  test("switch IT/EN: html.lang, stringhe e persistenza dopo refresh", async ({
    page,
    session,
  }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await page.goto("/settings");
    const select = page.locator("#newray-language-settings");
    await expect(page.getByRole("heading", { name: "Impostazioni" })).toBeVisible();

    await select.selectOption("en");
    expect(await page.locator("html").getAttribute("lang")).toBe("en");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

    // La preferenza persiste: refresh in inglese.
    await page.reload();
    expect(await page.locator("html").getAttribute("lang")).toBe("en");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

    // Rientro in italiano.
    await page.locator("#newray-language-settings").selectOption("it");
    expect(await page.locator("html").getAttribute("lang")).toBe("it");
    await expect(page.getByRole("heading", { name: "Impostazioni" })).toBeVisible();
  });
});

test.describe("Navigazione — focus e tastiera", () => {
  test("focus sull'h1 dopo la navigazione", async ({ page, session }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await page.goto("/conversations");
    await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();

    await page.getByRole("link", { name: "Impostazioni" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole("heading", { name: "Impostazioni" })).toBeFocused();
  });
});

test.describe("Navigazione — layout mobile", () => {
  test("menu mobile accessibile: aria-expanded e pannello controllato", async ({
    page,
    session,
  }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/conversations");
    await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();

    const menuButton = page.getByRole("button", { name: "Menu" });
    await expect(menuButton).toBeVisible();
    await expect(menuButton).toHaveAttribute("aria-expanded", "false");
    await expect(menuButton).toHaveAttribute("aria-controls", "newray-mobile-nav");

    await menuButton.click();
    await expect(menuButton).toHaveAttribute("aria-expanded", "true");
    const nav = page.locator("#newray-mobile-nav");
    await expect(nav).toBeVisible();
    await expect(nav.getByRole("link", { name: "Impostazioni" })).toBeVisible();
    // Le capacità future restano dichiarate (testo + stato), non cliccabili.
    // Nota: i due span non hanno spazio nel DOM (flex), quindi il testo
    // concatenato è "Documentiin corso": si seleziona il solo "Documenti".
    await expect(nav.getByText("Documenti")).toBeVisible();
  });
});
