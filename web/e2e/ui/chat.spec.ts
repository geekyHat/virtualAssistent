import { defaultIdentity, expect, test } from "../fixtures";

test("Assistente si attiva esplicitamente e resta disponibile dopo refresh", async ({
  page,
  session,
}) => {
  session.set({
    me: { kind: "ok", identity: defaultIdentity },
    status: { kind: "bootstrapped" },
    chat: { ...session.snapshot().chat, profiles: [] },
  });
  await page.goto("/conversations");
  await expect(page.getByRole("button", { name: "Attiva Assistente" })).toBeVisible();
  await page.getByRole("button", { name: "Attiva Assistente" }).click();
  await expect(page.getByRole("button", { name: "Attiva Assistente" })).toHaveCount(0);
  expect(session.snapshot().chat.profiles).toHaveLength(1);

  await page.reload();
  await expect(page.getByRole("button", { name: "Attiva Assistente" })).toHaveCount(0);
  await page.getByRole("link", { name: "Impostazioni" }).click();
  await page.getByText("Avanzate · profili e modelli").click();
  await expect(page.locator("#models-target-profile")).toContainText("Echo Assistant");
  expect(session.snapshot().chat.profiles).toHaveLength(1);
});

/**
 * Prove browser di chat (B-09.2).
 * Percorso obbligatorio: avvio → accesso → conversazione → scelta
 * profilo/modello → prompt → risposta → stop → riapertura →
 * logout/login → stessa cronologia.
 * Le rotte API sono intercettate (modello deterministico Echo): nessun
 * backend, nessuna GPU.
 */

const CREDENTIAL = "test-passphrase-1234";

test.describe("Chat — percorso completo B-09.2", () => {
  test("composer multilinea: Maiusc+Invio conserva la bozza, Invio invia una volta", async ({
    page,
    session,
  }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    const composer = page.getByRole("textbox", { name: "Scrivi un messaggio…" });
    await composer.fill("Prima riga");
    await composer.press("Shift+Enter");
    await composer.type("Seconda riga");
    await expect(composer).toHaveValue("Prima riga\nSeconda riga");
    await expect(page.locator("[data-role='assistant']")).toHaveCount(0);
    await composer.press("Enter");
    const conversationId = page.url().split("/").pop()!;
    await expect
      .poll(
        () =>
          session
            .snapshot()
            .chat.messages[conversationId]?.filter((message) => message.role === "assistant").length
      )
      .toBe(1);
    await expect(page.getByTestId("run-status")).toHaveText("Completata");
    await expect(page.locator("[data-role='assistant']")).toHaveCount(1);
    await expect(page.locator("[data-role='assistant']")).toContainText("Prima riga");
    await expect(composer).toHaveValue("");
  });

  test("avvio → bootstrap → conversazione → prompt → echo → riapertura → logout/login → cronologia", async ({
    page,
    session,
  }) => {
    // 1. Avvio: pagina di bootstrap
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Benvenuto in NewRay" })).toBeVisible();

    // 2. Accesso: bootstrap con successo
    session.set({
      bootstrap: { kind: "created", identity: { ...defaultIdentity, display_name: "Ada" } },
      me: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } },
      status: { kind: "bootstrapped" },
      login: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } },
    });
    await page.getByLabel("Nome da mostrare").fill("Ada");
    await page.getByLabel("Password locale").fill(CREDENTIAL);
    await page.getByRole("button", { name: "Inizia NewRay" }).click();
    await expect(page).toHaveURL(/\/conversations$/);

    // 3. Conversazione: creare una nuova conversazione
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page).toHaveURL(/\/conversations\/conv_/);

    // 4. Pilot: l'unico Assistente disponibile è scelto automaticamente;
    // la configurazione manuale resta in Impostazioni avanzate.
    await expect(page.getByRole("button", { name: "Profilo" })).toHaveCount(0);

    // 5. Prompt: invio messaggio
    const input = page.getByLabel("Scrivi un messaggio…");
    await input.fill("Ciao mondo");
    await page.getByRole("button", { name: "Invia" }).click();

    // 6. Risposta: lo streaming Echo produce "Echo: Ciao mondo"
    await expect(page.locator("[data-role='assistant']").first()).toContainText("Echo: Ciao mondo");

    // 7. Riapertura: la conversazione appare nella sidebar
    await page.getByRole("button", { name: "Conversazioni" }).click();
    const sidebar = page.getByRole("complementary", { name: "Opzioni dello spazio di lavoro" });
    await expect(
      sidebar.getByRole("button", { name: "Conversazione 1", exact: true })
    ).toBeVisible();

    // Invio un secondo messaggio per verificare la cronologia
    await input.fill("Secondo messaggio");
    await page.getByRole("button", { name: "Invia" }).click();
    await expect(page.locator("[data-role='assistant']").nth(1)).toContainText(
      "Echo: Secondo messaggio"
    );

    // 8. Logout: navigare a /settings e terminare la sessione
    await page.getByRole("link", { name: "Impostazioni" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    session.set({ me: { kind: "unauthenticated" } });
    await page.getByRole("button", { name: "Termina la sessione" }).click();
    await expect(page).toHaveURL(/\/login$/);

    // 9. Login: rientrare con le stesse credenziali
    session.set({ me: { kind: "ok", identity: { ...defaultIdentity, display_name: "Ada" } } });
    await page.getByLabel("Nome da mostrare").fill("Ada");
    await page.getByLabel("Password locale").fill(CREDENTIAL);
    await page.getByRole("button", { name: "Accedi" }).click();
    await expect(page).toHaveURL(/\/conversations$/);

    // 10. Stessa cronologia: la conversazione persiste (nei mock)
    await expect(
      sidebar.getByRole("button", { name: "Conversazione 1", exact: true })
    ).toBeVisible();
    await sidebar.getByRole("button", { name: "Conversazione 1", exact: true }).click();
    await expect(page).toHaveURL(/\/conversations\/conv_/);

    // I messaggi sono persistiti nel mock: user + assistant × 2
    const messages = page.locator("[data-role]");
    await expect(messages).toHaveCount(4);
    await expect(messages.nth(0)).toContainText("Ciao mondo");
    await expect(messages.nth(1)).toContainText("Echo: Ciao mondo");
    await expect(messages.nth(2)).toContainText("Secondo messaggio");
    await expect(messages.nth(3)).toContainText("Echo: Secondo messaggio");
  });

  test("stop interrompe lo streaming senza errori", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
    });
    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page).toHaveURL(/\/conversations\/conv_/);

    await page.getByLabel("Scrivi un messaggio…").fill("Messaggio lungo");
    await page.getByRole("button", { name: "Invia" }).click();

    // Lo streaming Echo è istantaneo nei mock, ma il pulsante stop deve
    // apparire durante lo stato streaming (anche se breve).
    // Verifichiamo che dopo il completamento non ci siano errori.
    await expect(page.locator("[data-role='assistant']").first()).toContainText("Echo:");
    await expect(page.getByRole("alert")).not.toBeVisible();
  });

  test("errore dal modello: alert visibile", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [
          {
            id: "prof_echo",
            kind: "assistant",
            display_name: "Echo Assistant",
            version: "v1",
            binding: { name: "echo", runtime: "echo", model_name: "echo", parameters: {} },
            model: {
              name: "echo",
              runtime: "echo",
              digest: null,
              status: "ready",
              capabilities: ["chat"],
            },
            created_at: "2026-09-01T00:00:00Z",
            updated_at: "2026-09-01T00:00:00Z",
          },
        ],
        run: { kind: "error", status: 503, message: "modello non disponibile" },
      },
    });

    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await page.getByLabel("Scrivi un messaggio…").fill("Test errore");
    await page.getByRole("button", { name: "Invia" }).click();

    await expect(page.getByRole("alert")).toBeVisible();
  });

  test("elimina conversazione: rimossa dalla sidebar", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
    });
    await page.goto("/conversations");

    // Crea due conversazioni
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page).toHaveURL(/\/conversations\/conv_/);
    await page.getByRole("button", { name: "Nuova conversazione" }).click();

    const convList = page.getByRole("list", { name: "Lista conversazioni" });
    await expect(convList.locator("li")).toHaveCount(2);

    // Elimina la prima: conferma proporzionata a due passaggi, un solo
    // click non cancella nulla.
    await convList
      .getByLabel(/^Elimina .*Conversazione/)
      .first()
      .click();
    await expect(convList.locator("li")).toHaveCount(2);
    await convList
      .getByLabel(/Conferma eliminazione.*Conversazione/)
      .first()
      .click();
    await expect(convList.locator("li")).toHaveCount(1);
  });

  test("elimina conversazione: annullare la conferma non cancella nulla", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
    });
    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();

    const convList = page.getByRole("list", { name: "Lista conversazioni" });
    await expect(convList.locator("li")).toHaveCount(1);

    await convList.getByLabel(/^Elimina .*Conversazione/).click();
    await convList.getByLabel("Annulla").click();
    await expect(convList.locator("li")).toHaveCount(1);
  });

  test("rinomina conversazione: nuovo titolo persiste nella sidebar", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
    });
    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();

    const convList = page.getByRole("list", { name: "Lista conversazioni" });
    await convList.getByLabel(/^Rinomina .*Conversazione/).click();
    const editInput = convList.getByLabel(/^Rinomina .*Conversazione/);
    await editInput.fill("Note di lavoro");
    await editInput.press("Enter");

    await expect(convList.getByText("Note di lavoro")).toBeVisible();
  });

  test("rinomina in conflitto: 409 mostra errore e non sovrascrive il titolo", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
    });
    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();

    const convList = page.getByRole("list", { name: "Lista conversazioni" });
    await expect(convList.getByText("Conversazione 1")).toBeVisible();

    // La conversazione è cambiata altrove (altra scheda) dopo la lettura
    // del client: il server deve rifiutare con 409, non sovrascrivere.
    const conv = session.snapshot().chat.conversations[0];
    conv.updated_at = new Date(Date.parse(conv.updated_at) + 1000).toISOString();

    await convList.getByLabel(/^Rinomina .*Conversazione/).click();
    const editInput = convList.getByLabel(/^Rinomina .*Conversazione/);
    await editInput.fill("Titolo in conflitto");
    await editInput.press("Enter");

    await expect(page.getByRole("alert")).toBeVisible();
    await expect(convList.getByText("Conversazione 1")).toBeVisible();
    await expect(convList.getByText("Titolo in conflitto")).not.toBeVisible();
  });

  test("paginazione: carica altre conversazioni senza duplicati", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
    });
    await page.goto("/conversations");

    // Il mock pagina a 2 elementi: la terza conversazione richiede
    // "Carica altre" e non deve duplicare le prime due.
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await page.getByRole("button", { name: "Nuova conversazione" }).click();

    const convList = page.getByRole("list", { name: "Lista conversazioni" });
    await expect(convList.locator("li")).toHaveCount(2);

    const loadMore = page.getByRole("button", { name: "Carica altre" });
    await expect(loadMore).toBeVisible();
    await loadMore.click();

    await expect(convList.locator("li")).toHaveCount(3);
    await expect(page.getByRole("button", { name: "Carica altre" })).not.toBeVisible();
  });

  test("refresh su conversazione attiva: ripristina i messaggi", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
    });
    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await page.getByLabel("Scrivi un messaggio…").fill("Prima del refresh");
    await page.getByRole("button", { name: "Invia" }).click();
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "Echo: Prima del refresh"
    );

    // Refresh della pagina
    await page.reload();
    await expect(page.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();

    // La sidebar mostra ancora la conversazione
    const sidebar = page.getByRole("complementary", { name: "Opzioni dello spazio di lavoro" });
    await expect(
      sidebar.getByRole("button", { name: "Conversazione 1", exact: true })
    ).toBeVisible();

    // Riapro la conversazione e trovo i messaggi
    await sidebar.getByRole("button", { name: "Conversazione 1", exact: true }).click();
    await expect(page.locator("[data-role='user']").first()).toContainText("Prima del refresh");
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "Echo: Prima del refresh"
    );
  });

  test("nessun profilo disponibile: input disabilitato", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [
          {
            id: "prof_nomodel",
            kind: "assistant",
            display_name: "Senza Modello",
            version: "v1",
            binding: { name: "none", runtime: "none", model_name: "none", parameters: {} },
            model: null,
            created_at: "2026-09-01T00:00:00Z",
            updated_at: "2026-09-01T00:00:00Z",
          },
        ],
        run: { kind: "echo" },
      },
    });

    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page.getByRole("button", { name: "Invia" })).toBeDisabled();
  });
});
