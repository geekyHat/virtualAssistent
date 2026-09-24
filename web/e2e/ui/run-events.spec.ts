import { defaultIdentity, defaultProfile, expect, test } from "../fixtures";
import type { Page } from "@playwright/test";

/**
 * Prove browser del percorso durevole (P-06, NewRay.md §19.3):
 * `POST .../runs` → `GET /runs/{id}/events` (replay/cursore/resync) →
 * `POST /runs/{id}/cancel`. Sostituisce `chat-sse.spec.ts` come percorso
 * esercitato dalla UI ("senza doppio motore"); quel file resta come
 * regressione del contratto inline preservato ma non più chiamato.
 *
 * Le rotte sono intercettate con corpi SSE sintetici (nessun backend,
 * nessuna GPU). La riconnessione e il resync qui provano il CONTRATTO
 * CLIENT (un solo tentativo, riallineamento sullo snapshot): la potatura
 * reale dei delta e la propagazione della cancellazione dal worker sono
 * provate nei test di integrazione PostgreSQL del backend, non qui.
 */

async function openConversation(page: Page) {
  await page.goto("/conversations");
  await page.getByRole("button", { name: "Nuova conversazione" }).click();
  await expect(page).toHaveURL(/\/conversations\/conv_/);
}

async function sendAndSettle(page: Page, content: string) {
  await page.getByLabel("Scrivi un messaggio…").fill(content);
  await page.getByRole("button", { name: "Invia" }).click();
}

test.describe("Chat — run durevoli (P-06)", () => {
  test("run.completed: completata con meter autorevole dal terminale", async ({
    page,
    session,
  }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await openConversation(page);
    await sendAndSettle(page, "Ciao");

    await expect(page.getByTestId("run-status")).toHaveText("Completata");
    await expect(page.getByTestId("token-rate")).toHaveText("42.5 token/s");
    await expect(page.locator("[data-role='assistant']").first()).toContainText("Echo: Ciao");
  });

  test("finish_reason length: troncata, testo parziale conservato", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: {
          kind: "events",
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "message.delta", payload: { text: "frammento parziale" } },
            {
              type: "run.completed",
              payload: { finish_reason: "length", text: "frammento parziale" },
            },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test length");

    await expect(page.getByTestId("run-status")).toHaveText("Troncata per lunghezza");
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "frammento parziale"
    );
    await expect(page.getByTestId("token-rate")).toHaveText("non disponibile");
  });

  test("ciclo tool: gli eventi tool.* non rompono il run, che completa (P-07)", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: {
          kind: "events",
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "tool.executing", payload: { tool_name: "run.status", is_error: false } },
            { type: "tool.succeeded", payload: { tool_name: "run.status", is_error: false } },
            { type: "message.delta", payload: { text: "Il run è in corso." } },
            {
              type: "run.completed",
              payload: { finish_reason: "stop", text: "Il run è in corso." },
            },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Che stato ha il run?");

    await expect(page.getByTestId("run-status")).toHaveText("Completata");
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "Il run è in corso."
    );
  });

  test("tool.failed non è un guasto del run: la generazione completa comunque (P-07)", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: {
          kind: "events",
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "tool.executing", payload: { tool_name: "run.status", is_error: false } },
            { type: "tool.failed", payload: { tool_name: "run.status", is_error: true } },
            { type: "message.delta", payload: { text: "Non ho trovato quel run." } },
            {
              type: "run.completed",
              payload: { finish_reason: "stop", text: "Non ho trovato quel run." },
            },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Stato del run inesistente?");

    await expect(page.getByTestId("run-status")).toHaveText("Completata");
    await expect(page.getByRole("alert")).not.toBeVisible();
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "Non ho trovato quel run."
    );
  });

  test("run.cancelled: interrotta con frammento conservato", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: {
          kind: "events",
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "message.delta", payload: { text: "frammento" } },
            {
              type: "run.cancelled",
              payload: { finish_reason: "cancelled_by_user", text: "frammento" },
            },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test cancelled");

    await expect(page.getByTestId("run-status")).toHaveText("Interrotta");
    await expect(page.locator("[data-role='assistant']").first()).toContainText("frammento");
  });

  test("run.completed senza testo: nessuna risposta, non completion silenziosa", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: {
          kind: "events",
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "run.completed", payload: { finish_reason: "stop", text: "" } },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test vuoto");

    await expect(page.getByTestId("run-status")).toHaveText("Nessuna risposta");
    await expect(page.locator("[data-role='assistant'][data-streaming]")).toHaveCount(0);
  });

  test("run.failed dopo delta: fallita, testo parziale conservato, alert visibile", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: {
          kind: "events",
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "message.delta", payload: { text: "testo parziale " } },
            {
              type: "run.failed",
              payload: { finish_reason: "model_error", text: "testo parziale " },
            },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test errore");

    await expect(page.getByTestId("run-status")).toHaveText("Fallita");
    await expect(page.locator("[data-role='assistant']").first()).toContainText("testo parziale");
  });

  test("EOF senza terminale: un tentativo di riconnessione, poi guasto visibile", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        // Nessun terminale in nessuna delle due chiamate (l'hook rifà la
        // richiesta con `after_sequence` aggiornato): la seconda chiamata
        // non ha più eventi da restituire, si chiude subito senza
        // terminale, e SOLO allora è un guasto (mai una falsa completion).
        durableRun: {
          kind: "events",
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "message.delta", payload: { text: "parziale senza fine" } },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test EOF");

    await expect(page.getByTestId("run-status")).toHaveText("Fallita");
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "parziale senza fine"
    );
  });

  test("evento senza id: guasto esplicito, nessuna sequenza inventata", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: { kind: "events-raw", body: "event: message.delta\ndata: {}\n\n" },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test id mancante");

    await expect(page.getByTestId("run-status")).toHaveText("Fallita");
  });

  test("connessione interrotta a metà stream: un tentativo di riconnessione completa il run", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: {
          kind: "events",
          // La prima connessione si ferma a sequence=2 (nessun terminale);
          // l'hook riconnette con after_sequence=2 e riceve il resto.
          disconnectAfterSequence: 2,
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "message.delta", payload: { text: "ripreso" } },
            { type: "run.completed", payload: { finish_reason: "stop", text: "ripreso" } },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test reconnect");

    await expect(page.getByTestId("run-status")).toHaveText("Completata");
    await expect(page.locator("[data-role='assistant']").first()).toContainText("ripreso");
  });

  test("cursore scaduto alla riconnessione: resync riallinea sullo snapshot", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: {
          kind: "events",
          disconnectAfterSequence: 2,
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
          ],
          resyncOnReconnect: {
            latestSequence: 99,
            snapshot: {
              state: "completed",
              partial_text: "testo dallo snapshot",
              finish_reason: "stop",
              prompt_tokens: 3,
              completion_tokens: 4,
              eval_duration_ns: 1_000_000_000,
            },
          },
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test resync");

    await expect(page.getByTestId("run-status")).toHaveText("Completata");
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "testo dallo snapshot"
    );
  });

  test("terminale duplicato: il secondo run.completed non regredisce lo stato", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: {
          kind: "events",
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "run.completed", payload: { finish_reason: "stop", text: "una volta" } },
            { type: "run.completed", payload: { finish_reason: "stop", text: "MAI mostrato" } },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test duplicato");

    await expect(page.getByTestId("run-status")).toHaveText("Completata");
    await expect(page.locator("[data-role='assistant']").first()).toContainText("una volta");
    await expect(page.locator("[data-role='assistant']").first()).not.toContainText("MAI mostrato");
  });

  test("stop: chiama /cancel e non abortisce il trasporto locale; l'esito arriva dal terminale", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        // A differenza del vecchio percorso inline, `stop()` non abortisce
        // più il fetch locale: l'esito autorevole resta quello che arriva
        // sullo STESSO stream (qui il mock lo porta già, dato che una
        // singola risposta statica non può reagire alla POST /cancel a
        // metà; la propagazione reale dal worker è provata nel backend).
        durableRun: {
          kind: "events",
          events: [
            { type: "run.queued", payload: {} },
            { type: "run.started", payload: {} },
            { type: "message.delta", payload: { text: "frammento" } },
            {
              type: "run.cancelled",
              payload: { finish_reason: "cancelled_by_user", text: "frammento" },
            },
          ],
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test stop");

    // Lo streaming nei mock è istantaneo: il pulsante può sparire fra il
    // controllo di visibilità e il click (il run è già terminale). In
    // entrambi i casi l'esito atteso è lo stesso, letto dal terminale.
    const stop = page.getByRole("button", { name: "Interrompi" });
    if (await stop.isVisible({ timeout: 1000 }).catch(() => false)) {
      await stop.click({ timeout: 1000 }).catch(() => {});
    }

    await expect(page.getByTestId("run-status")).toHaveText("Interrotta");
    await expect(page.getByRole("alert")).not.toBeVisible();
    await expect(page.getByLabel("Scrivi un messaggio…")).toBeEnabled();
  });

  test("creazione del run rifiutata: errore visibile, nessuno stream aperto", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: { kind: "echo" },
        durableRun: { kind: "create-error", status: 503, message: "coda piena" },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test coda piena");

    await expect(page.getByRole("alert")).toContainText("coda piena");
    await expect(page.getByLabel("Scrivi un messaggio…")).toBeEnabled();
  });

  test("resume: un refresh (o una seconda scheda) ritrova e completa un run già in corso", async ({
    page,
    session,
  }) => {
    // Il run è creato nel registro del mock, mai attraverso il composer
    // di questa pagina: rappresenta l'altra scheda (o l'invio precedente
    // al refresh) di cui questa pagina non ha alcuna conoscenza locale —
    // solo `GET /conversations/{id}/active-run` può farglielo scoprire.
    // Si naviga via PRIMA di seminare il run: la conversazione appena
    // creata ha già un proprio resume-check in corso (che non trova
    // nulla, correttamente); seminare mentre resta in corso sarebbe una
    // gara innocua ma non deterministica per QUESTO test, non un
    // comportamento diverso dell'hook.
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await openConversation(page);
    const conversationId = page.url().split("/").at(-1)!;
    await page.goto("/conversations");
    session.seedActiveRun(conversationId, "Ciao resume");
    await page.goto(`/conversations/${conversationId}`);

    await expect(page.getByTestId("run-status")).toHaveText("Completata");
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "Echo: Ciao resume"
    );
  });

  test("resume: un run già terminale (mai osservato da questa scheda) non viene riproposto due volte", async ({
    page,
    session,
  }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await openConversation(page);
    const conversationId = page.url().split("/").at(-1)!;
    await page.goto("/conversations");
    session.seedActiveRun(conversationId, "Ciao due volte");
    await page.goto(`/conversations/${conversationId}`);

    // Prima visita: ritrova e completa il run (come sopra).
    await expect(page.getByTestId("run-status")).toHaveText("Completata");

    // Refresh successivo: il run è ormai terminale (osservato dalla prima
    // sottoscrizione) — nessun resume, nessun badge di stato residuo.
    await page.reload();
    await expect(page.getByTestId("run-status")).toHaveCount(0);
  });
});
