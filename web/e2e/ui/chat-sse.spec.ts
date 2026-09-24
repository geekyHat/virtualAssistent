import { defaultIdentity, defaultProfile, expect, test } from "../fixtures";
import type { Page } from "@playwright/test";

/**
 * Prove browser dei guasti dello stream SSE (B-03.2-35; NewRay.md §18.4).
 *
 * Le rotte API sono intercettate con corpi SSE sintetici: nessun
 * backend, nessuna GPU. Copre i terminali distinti (`stop`, `length`,
 * `cancelled`, vuoto), `error` dopo delta (testo parziale conservato),
 * EOF senza terminale (guasto, non completion) e il meter token/s
 * (valore autorevole dal terminale, mai un zero inventato).
 */

const sse = (events: Array<{ event: string; data: Record<string, unknown> }>): string =>
  events.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join("");

async function openConversation(page: Page) {
  await page.goto("/conversations");
  await page.getByRole("button", { name: "Nuova conversazione" }).click();
  await expect(page).toHaveURL(/\/conversations\/conv_/);
}

async function sendAndSettle(page: Page, content: string) {
  await page.getByLabel("Scrivi un messaggio…").fill(content);
  await page.getByRole("button", { name: "Invia" }).click();
}

test.describe("Chat — guasti SSE (B-03.2-35)", () => {
  test("done stop: completata con meter autorevole dal terminale", async ({ page, session }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await openConversation(page);
    await sendAndSettle(page, "Ciao");

    await expect(page.getByTestId("run-status")).toHaveText("Completata");
    // Meter: valore autorevole dal terminale `done` (fixture echo: 42.5).
    await expect(page.getByTestId("token-rate")).toHaveText("42.5 token/s");
    await expect(page.locator("[data-role='assistant']").first()).toContainText("Echo: Ciao");
  });

  test("done length: troncata, non stop; testo parziale conservato", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: {
          kind: "sse",
          body: sse([
            { event: "delta", data: { text: "frammento " } },
            { event: "delta", data: { text: "parziale" } },
            {
              event: "done",
              data: {
                finish_reason: "length",
                tokens_per_second: null,
                prompt_tokens: null,
                completion_tokens: null,
                model: "echo",
                digest: null,
              },
            },
          ]),
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test length");

    await expect(page.getByTestId("run-status")).toHaveText("Troncata per lunghezza");
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "frammento parziale"
    );
    // Metrica non disponibile: mai un zero inventato.
    await expect(page.getByTestId("token-rate")).toHaveText("non disponibile");
  });

  test("done cancelled: interrotta dal server con frammento", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: {
          kind: "sse",
          body: sse([
            { event: "delta", data: { text: "frammento" } },
            {
              event: "done",
              data: {
                finish_reason: "cancelled",
                tokens_per_second: null,
                prompt_tokens: null,
                completion_tokens: null,
                model: null,
                digest: null,
              },
            },
          ]),
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test cancelled");

    await expect(page.getByTestId("run-status")).toHaveText("Interrotta");
    await expect(page.locator("[data-role='assistant']").first()).toContainText("frammento");
  });

  test("done senza testo: terminale vuoto, non completion", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: {
          kind: "sse",
          body: sse([
            {
              event: "done",
              data: {
                finish_reason: "stop",
                tokens_per_second: null,
                prompt_tokens: null,
                completion_tokens: null,
                model: "echo",
                digest: null,
              },
            },
          ]),
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test vuoto");

    await expect(page.getByTestId("run-status")).toHaveText("Nessuna risposta");
    // Nessun messaggio assistant di streaming (testo vuoto).
    await expect(page.locator("[data-role='assistant'][data-streaming]")).toHaveCount(0);
  });

  test("error dopo delta: fallita con testo parziale conservato e alert", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: {
          kind: "sse",
          body: sse([
            { event: "delta", data: { text: "testo parziale " } },
            { event: "error", data: { code: "MODEL_ERROR", message: "modello in errore" } },
          ]),
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test errore");

    await expect(page.getByTestId("run-status")).toHaveText("Fallita");
    await expect(page.getByRole("alert")).toContainText("modello in errore");
    // Il testo parziale resta visibile, non viene scartato.
    await expect(page.locator("[data-role='assistant']").first()).toContainText("testo parziale");
  });

  test("EOF senza terminale: guasto visibile, mai falsa completion", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        run: {
          kind: "sse",
          body: sse([{ event: "delta", data: { text: "parziale senza fine" } }]),
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test EOF");

    await expect(page.getByTestId("run-status")).toHaveText("Connessione chiusa senza terminale");
    await expect(page.locator("[data-role='assistant']").first()).toContainText(
      "parziale senza fine"
    );
    // EOF non è un errore del modello: nessun alert.
    await expect(page.getByRole("alert")).not.toBeVisible();
  });

  test("stop durante lo streaming: nessun errore, input riabilitato", async ({ page, session }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: [defaultProfile],
        // Stream senza terminale: l'esito dipende dalla corsa tra abort
        // locale e chiusura del mock; in entrambi i casi nessun errore.
        run: {
          kind: "sse",
          body: sse([{ event: "delta", data: { text: "frammento" } }]),
        },
      },
    });
    await openConversation(page);
    await sendAndSettle(page, "Test stop");

    // Lo streaming nei mock è istantaneo: clicchiamo stop appena
    // visibile; se il run è già terminato, lo stato è quello del
    // terminale. In ogni caso: nessun alert e input riabilitato.
    const stop = page.getByRole("button", { name: "Interrompi" });
    if (await stop.isVisible({ timeout: 1000 }).catch(() => false)) {
      await stop.click();
    }

    await expect(page.getByRole("alert")).not.toBeVisible();
    await expect(page.getByLabel("Scrivi un messaggio…")).toBeEnabled();
  });
});
