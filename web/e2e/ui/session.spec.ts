import type { Page } from "@playwright/test";
import { defaultIdentity, expect, test } from "../fixtures";

/**
 * Prove browser di sessione (B-09.1 + B-03.2-14 + B-08.1).
 * Le rotte API sono intercettate: nessun backend è avviato.
 *
 * Flusso aggiornato (B-08.1): bootstrap/login di successo portano a
 * `/conversations`; il termine sessione vive in `/settings`.
 */

const CREDENTIAL = "test-passphrase-1234";

async function gotoSignedOut(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Benvenuto in NewRay" })).toBeVisible();
}

async function fillBootstrap(page: Page, name = "Ada", credential = CREDENTIAL) {
  await page.getByLabel("Nome da mostrare").fill(name);
  await page.getByLabel("Password locale").fill(credential);
}

test.describe("Sessione — bootstrap", () => {
  // La navigazione avviene dentro ogni test: prima si attende esplicitamente
  // lo stato iniziale (signed-out o signed-in), altrimenti la /me in flight
  // può risolversi con lo scenario appena cambiato e la UI salta stato.

  test("stato non autenticato: form di bootstrap accessibile", async ({ page, session }) => {
    void session;
    await gotoSignedOut(page);
    const nameInput = page.getByLabel("Nome da mostrare");
    const credentialInput = page.getByLabel("Password locale");
    await expect(nameInput).toBeVisible();
    await expect(credentialInput).toBeVisible();
    const submit = page.getByRole("button", { name: "Inizia NewRay" });
    await expect(submit).toBeDisabled();

    await nameInput.fill("Ada");
    // Solo nome: non basta.
    await expect(submit).toBeDisabled();
    await credentialInput.fill(CREDENTIAL);
    await expect(submit).toBeEnabled();
  });

  test("bootstrap 201 → transizione a /conversations", async ({ page, session }) => {
    await gotoSignedOut(page);
    session.set({
      bootstrap: { kind: "created", identity: { ...defaultIdentity, display_name: "Ada" } },
      me: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } },
    });

    await fillBootstrap(page);
    await page.getByRole("button", { name: "Inizia NewRay" }).click();

    await expect(page).toHaveURL(/\/conversations$/);
    await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();
  });

  test("bootstrap 500: errore alert focalizzato, retry ripristina", async ({ page, session }) => {
    await gotoSignedOut(page);
    session.set({ bootstrap: { kind: "server-error", message: "guasto simulato" } });

    await fillBootstrap(page);
    await page.getByRole("button", { name: "Inizia NewRay" }).click();

    const alert = page.getByRole("alert").filter({ hasText: "inizializzare NewRay" });
    await expect(alert).toBeVisible();
    await expect(alert).toBeFocused();
    // Il nome inserito non viene perso: retry parte da lì.
    await expect(page.getByLabel("Nome da mostrare")).toHaveValue("Ada");

    session.set({
      bootstrap: { kind: "created", identity: { ...defaultIdentity, display_name: "Ada" } },
      me: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } },
    });
    await page.getByRole("button", { name: "Riprova l'inizializzazione" }).click();
    await expect(page).toHaveURL(/\/conversations$/);
    await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();
  });

  test("bootstrap 409: schermata già inizializzato con Accedi e Rileva", async ({
    page,
    session,
  }) => {
    await gotoSignedOut(page);
    session.set({ bootstrap: { kind: "conflict" }, status: { kind: "bootstrapped" } });

    await fillBootstrap(page);
    await page.getByRole("button", { name: "Inizia NewRay" }).click();

    await expect(page.getByRole("heading", { name: /già stato inizializzato/i })).toBeVisible();
    await expect(page.getByRole("button", { name: "Accedi" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Rileva la sessione" })).toBeVisible();
  });

  test("bootstrap: doppio click non genera invii multipli", async ({ page, session, context }) => {
    let bootstrapCount = 0;
    await context.route("**/api/v1/session", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      bootstrapCount += 1;
      await new Promise((resolve) => setTimeout(resolve, 400));
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ ...defaultIdentity, display_name: "Ada" }),
      });
    });
    await gotoSignedOut(page);
    session.set({ me: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } } });

    await fillBootstrap(page);
    const submit = page.getByRole("button", { name: "Inizia NewRay" });
    // Primo click accettato dal componente; i successivi trovano il pulsante
    // disabilitato (bootstrapPending). Usiamo `force + timeout basso` per non
    // attendere l'abilitazione, che non arriverà finché la mutation risolve.
    await submit.click();
    await submit.click({ force: true, timeout: 500 }).catch(() => undefined);
    await submit.click({ force: true, timeout: 500 }).catch(() => undefined);

    await expect(page).toHaveURL(/\/conversations$/);
    expect(bootstrapCount).toBe(1);
  });

  test(
    "B-03.2-33: due submit sincroni nello stesso tick (requestSubmit×2) " +
      "avviano un solo bootstrap",
    async ({ page, session, context }) => {
      // A differenza del test precedente (due `.click()` di Playwright, ognuno
      // con un proprio round-trip che lascia a React tutto il tempo di
      // ri-renderizzare `disabled`), qui i due `requestSubmit()` corrono nello
      // stesso task del browser: è la vera finestra diagnosticata in B-03.2-33,
      // dove solo una guardia sincrona (non `isPending`) può bastare.
      let bootstrapCount = 0;
      await context.route("**/api/v1/session", async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        bootstrapCount += 1;
        await route.fulfill({
          status: 201,
          contentType: "application/json",
          body: JSON.stringify({ ...defaultIdentity, display_name: "Ada" }),
        });
      });
      await gotoSignedOut(page);
      session.set({ me: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } } });
      await fillBootstrap(page);

      await page.evaluate(() => {
        const form = document.querySelector("form");
        form?.requestSubmit();
        form?.requestSubmit();
      });

      await expect(page).toHaveURL(/\/conversations$/);
      expect(bootstrapCount).toBe(1);
    }
  );

  test("rete interrotta al bootstrap: errore recuperabile", async ({ page, session }) => {
    await gotoSignedOut(page);
    session.set({ bootstrap: { kind: "network-error" } });

    await fillBootstrap(page);
    await page.getByRole("button", { name: "Inizia NewRay" }).click();

    await expect(page.getByRole("alert").filter({ hasText: "inizializzare NewRay" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Riprova l'inizializzazione" })).toBeVisible();
  });

  test("navigazione tastiera: Tab arriva al pulsante e Invio invia", async ({ page, session }) => {
    await gotoSignedOut(page);
    session.set({
      bootstrap: { kind: "created", identity: { ...defaultIdentity, display_name: "Ada" } },
      me: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } },
    });

    await page.getByLabel("Nome da mostrare").focus();
    await page.keyboard.type("Ada");
    await page.keyboard.press("Tab");
    await page.keyboard.type(CREDENTIAL);
    await page.keyboard.press("Enter");

    await expect(page).toHaveURL(/\/conversations$/);
    await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();
  });
});

test.describe("Sessione — logout e login (B-03.2-14, B-08.1)", () => {
  test("revoca 500 in /settings: errore alert e nessuna falsa conferma di logout", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      revoke: { kind: "server-error" },
    });
    await page.goto("/settings");

    await expect(page.getByRole("heading", { name: "Impostazioni" })).toBeVisible();
    await page.getByRole("button", { name: "Termina la sessione" }).click();

    const alert = page.getByRole("alert").filter({ hasText: "terminare la sessione" });
    await expect(alert).toBeVisible();
    await expect(alert).toBeFocused();
    // L'identità resta visibile: il logout non è stato confermato dal server.
    await expect(page.getByRole("heading", { name: "Impostazioni" })).toBeVisible();
  });

  test(
    "sessione scaduta durante l'uso: un 401 su una chiamata protetta " +
      "qualunque riporta a /login (B-03.2-26)",
    async ({ page, session }) => {
      session.set({
        me: { kind: "ok", identity: defaultIdentity },
        status: { kind: "bootstrapped" },
      });
      await page.goto("/conversations");
      await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();

      // La sessione scade lato server mentre l'utente è già sulla pagina:
      // non è un logout esplicito, la scopre la prossima chiamata protetta
      // (qui la creazione di una conversazione, non /api/v1/me).
      session.set({ me: { kind: "unauthenticated" } });
      await page.getByRole("button", { name: "Nuova conversazione" }).click();

      await expect(page).toHaveURL(/\/login$/);
    }
  );

  test("signed-out + bootstrappato: la landing rimanda a /login", async ({ page, session }) => {
    session.set({
      me: { kind: "unauthenticated" },
      status: { kind: "bootstrapped" },
    });
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "Accedi a NewRay" })).toBeVisible();
  });

  test("bootstrap → logout → login: stessi dati", async ({ page, session }) => {
    // 1) Bootstrap
    await gotoSignedOut(page);
    session.set({
      bootstrap: { kind: "created", identity: { ...defaultIdentity, display_name: "Ada" } },
      me: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } },
      status: { kind: "bootstrapped" },
      login: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } },
    });
    await fillBootstrap(page);
    await page.getByRole("button", { name: "Inizia NewRay" }).click();
    await expect(page).toHaveURL(/\/conversations$/);

    // 2) Logout da /settings: navigazione SPA (niente full reload, che
    //    rifarebbe /me con lo scenario già cambiato). Dopo il 204 la /me
    //    torna 401 e la UI si porta su /login.
    await page.getByRole("link", { name: "Impostazioni" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    session.set({ me: { kind: "unauthenticated" } });
    await page.getByRole("button", { name: "Termina la sessione" }).click();
    await expect(page).toHaveURL(/\/login$/);

    // 3) Login
    session.set({ me: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } } });
    await page.getByLabel("Nome da mostrare").fill("Ada");
    await page.getByLabel("Password locale").fill(CREDENTIAL);
    await page.getByRole("button", { name: "Accedi" }).click();
    await expect(page).toHaveURL(/\/conversations$/);
    await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();
  });

  test("login 401: alert 'Nome o password non validi'", async ({ page, session }) => {
    session.set({
      me: { kind: "unauthenticated" },
      status: { kind: "bootstrapped" },
      login: { kind: "invalid" },
    });
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Accedi a NewRay" })).toBeVisible();

    await page.getByLabel("Nome da mostrare").fill("Ada");
    await page.getByLabel("Password locale").fill("credenziale-sbagliata");
    await page.getByRole("button", { name: "Accedi" }).click();

    const alert = page.getByRole("alert").filter({ hasText: /nome o password non validi/i });
    await expect(alert).toBeVisible();
    await expect(alert).toBeFocused();
  });

  test("login credential-not-set: guida al recupero esplicito", async ({ page, session }) => {
    session.set({
      me: { kind: "unauthenticated" },
      status: { kind: "bootstrapped" },
      login: { kind: "credential-not-set" },
    });
    await page.goto("/login");
    await page.getByLabel("Nome da mostrare").fill("Ada");
    await page.getByLabel("Password locale").fill(CREDENTIAL);
    await page.getByRole("button", { name: "Accedi" }).click();

    const alert = page.getByRole("alert").filter({ hasText: /password locale/i });
    await expect(alert).toBeVisible();
    // Nessuna offerta di "nuovo bootstrap" da questa schermata: il
    // percorso è locale a CLI, come dichiarato dal messaggio.
  });
});
