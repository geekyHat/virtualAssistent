import { expect, test } from "@playwright/test";

/**
 * Prova live opt-in (B-09.1 → B-09.2 la estenderà).
 * Richiede `.start` avviato e `NEWRAY_E2E_LIVE=1`. Non si usa un account
 * personale: il test bootstrap un owner con nome sintetico su un cluster
 * sacrificabile e chiude la sessione al termine.
 *
 * Bersaglio: `NEWRAY_E2E_LIVE_BASE` (default http://127.0.0.1:5173).
 * Convenzione: eseguirlo su un launcher isolato, non sul dato personale.
 *
 * Sequenza (contratto B-03.2-14):
 *   1. `/` → se non bootstrappato, form di bootstrap (nome + password ≥ 8).
 *   2. Click "Inizia NewRay" → POST /bootstrap → cookie di sessione.
 *      L'app atterra da sola su /conversations (onSuccess di IdentityPage);
 *      è quel redirect il segnale che il POST è commitato.
 *   3. /settings → pannello "Sessione attiva" + nome owner + revoca.
 *   4. Revoca (204) → la sessione è chiusa ma l'owner persiste
 *      (bootstrapped=true): IdentityPage porta a /login, non al bootstrap.
 */

test.describe("Sessione live @live", () => {
  test.skip(process.env.NEWRAY_E2E_LIVE !== "1", "opt-in via NEWRAY_E2E_LIVE=1");

  test("bootstrap → me → revoca su PostgreSQL reale", async ({ page, request }) => {
    const name = `e2e-${Date.now()}`;

    // Skip guard: l'unico segnale pubblico di "già inizializzato" è
    // /session/status (il GET /me senza cookie risponde sempre 401 e non
    // può distinguere un cluster vuoto da uno bootstrappato).
    const statusProbe = await request.get("/api/v1/session/status");
    if (statusProbe.status() === 200 && (await statusProbe.json()).bootstrapped) {
      test.skip(true, "cluster già inizializzato");
    }

    await page.goto("/");

    // Il bootstrap (B-03.2-14) non accetta il solo nome come credenziale:
    // il pannello richiede anche una password locale di almeno 8 caratteri.
    await page.getByLabel("Nome da mostrare").fill(name);
    await page.getByLabel("Password locale").fill("e2e-synthetic-credential");
    await page.getByRole("button", { name: "Inizia NewRay" }).click();

    // Segnale di commit del POST: IdentityPage naviga da solo a
    // /conversations solo dopo il 200 del bootstrap (cookie già set).
    // Finché non arriva qui, un goto("/settings") ricaricherebbe la pagina
    // a metà POST e /me risponderebbe 401 → redirect a /login.
    await expect(page).toHaveURL(/\/conversations/);
    await expect(page.getByRole("heading", { name: "Conversazioni" })).toBeVisible();

    // A questo punto la sessione è commitata: /settings è protetto da
    // RequireSession e mostra il pannello identità.
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Sessione attiva" })).toBeVisible();
    // Il nome owner compare sia nel pannello impostazioni sia nel footer
    // della sidebar: limitiamo l'assert al contenuto principale.
    await expect(page.getByRole("main").getByText(name)).toBeVisible();

    await page.getByRole("button", { name: "Termina la sessione" }).click();

    // La revoca (204) chiude la sessione ma l'owner persiste: bootstrapped
    // resta true, quindi IdentityPage non mostra il pannello di bootstrap
    // ("Benvenuto in NewRay") ma porta a /login.
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "Accedi a NewRay" })).toBeVisible();
  });
});
