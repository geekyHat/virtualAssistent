import type { Route } from "@playwright/test";
import { defaultIdentity, expect, test } from "../fixtures";

test.describe("B-08.8 — workspace modulare", () => {
  test("desktop: pannello destro separato, collapse senza perdita della bozza e cambio conversazione isolato", async ({
    page,
    session,
  }, testInfo) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/conversations");

    const main = page.getByRole("main");
    const options = page.getByRole("complementary", { name: "Opzioni dello spazio di lavoro" });
    const navigation = page.getByRole("navigation", { name: "Navigazione" });
    await expect(navigation.getByRole("button", { name: "Nuova conversazione" })).toBeVisible();
    await expect(navigation.getByRole("listitem").first()).toContainText("Nuova conversazione");
    await expect(options.getByRole("button", { name: "Nuova conversazione" })).toHaveCount(0);
    await expect(main.getByRole("heading", { name: "Conversazioni", level: 1 })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("workspace-desktop-1440.png"),
      animations: "disabled",
    });
    await navigation.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page).toHaveURL(/\/conversations\/conv_\d+$/);
    const firstUrl = page.url();
    const composer = page.getByLabel("Scrivi un messaggio…");
    await composer.fill("Bozza conservata");

    await options.getByRole("button", { name: "Comprimi opzioni" }).click();
    await expect(composer).toHaveValue("Bozza conservata");
    await options.getByRole("button", { name: "Espandi opzioni" }).click();
    await expect(composer).toHaveValue("Bozza conservata");

    await composer.fill("Messaggio nella prima conversazione");
    await page.getByRole("button", { name: "Invia" }).click();
    await expect(page.locator("[data-role='assistant']").first()).toContainText("Echo:");
    await navigation.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page).not.toHaveURL(firstUrl);
    await expect(page.locator("[data-role='assistant']")).toHaveCount(0);
    await options
      .getByRole("list", { name: "Lista conversazioni" })
      .getByText("Conversazione 1")
      .click();
    await expect(page).toHaveURL(firstUrl);
    await expect(page.locator("[data-role='assistant']").first()).toContainText("Echo:");
  });

  test("mobile: drawer accessibile, chiusura Escape e focus restituito", async ({
    page,
    session,
  }, testInfo) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/conversations");

    const trigger = page.getByRole("button", { name: "Apri opzioni" });
    await trigger.click();
    const drawer = page.getByRole("dialog", { name: "Opzioni dello spazio di lavoro" });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("button", { name: "Chiudi opzioni" })).toBeFocused();
    await page.screenshot({
      path: testInfo.outputPath("workspace-mobile-375.png"),
      animations: "disabled",
    });
    await page.keyboard.press("Escape");
    await expect(drawer).not.toBeVisible();
    await expect(trigger).toBeFocused();

    await page.getByRole("button", { name: "Menu" }).click();
    const navigation = page.getByRole("navigation", { name: "Navigazione" });
    await expect(navigation.getByRole("listitem").first()).toContainText("Nuova conversazione");
    await navigation.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page.getByRole("navigation", { name: "Navigazione" })).toHaveCount(0);
    await expect(page.getByLabel("Scrivi un messaggio…")).toBeVisible();
    const inputBox = await page.getByLabel("Scrivi un messaggio…").boundingBox();
    const triggerBox = await trigger.boundingBox();
    expect(inputBox).not.toBeNull();
    expect(triggerBox).not.toBeNull();
    expect(triggerBox!.y + triggerBox!.height).toBeLessThan(inputBox!.y);
  });

  test("profilo e modello: cambio esplicito e conflitto 409 visibile", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        conversations: [],
        messages: {},
        run: { kind: "echo" },
        profiles: [{ ...session.snapshot().chat.profiles[0] }],
        models: [
          {
            name: "echo",
            runtime: "echo",
            digest: "sha256:echo000",
            status: "qualified",
            capabilities: ["chat"],
          },
          {
            name: "echo-alt",
            runtime: "echo",
            digest: "sha256:alt000",
            status: "qualified",
            capabilities: ["chat"],
          },
        ],
      },
    });
    await page.goto("/settings");
    await page.getByText("Avanzate · profili e modelli").click();
    const models = page.getByRole("region", { name: "Modelli" });
    await expect(page.locator("#models-target-profile")).toContainText("Echo Assistant");
    await expect(models.getByText("echo-alt")).toBeVisible();
    await models.getByRole("list").getByRole("button", { name: "Usa" }).click();
    await expect.poll(() => session.snapshot().chat.profiles[0].version).toBe("v2");
    await expect(models.getByRole("list").getByRole("button", { name: "Attuale" })).toHaveCount(1);
    await expect(models.getByRole("list").getByRole("button", { name: "Usa" })).toBeEnabled();

    // Simula una versione cambiata in un'altra scheda prima del secondo switch.
    session.snapshot().chat.profiles[0].version = "v3";
    await models.getByRole("list").getByRole("button", { name: "Usa" }).click();
    await expect(models.getByRole("alert")).toContainText("versione del profilo cambiata");
  });

  test("catalogo modelli: errore recuperabile nel solo pannello", async ({ page, session }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    const failModels = async (route: Route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          code: "INTERNAL_ERROR",
          message: "Catalogo non disponibile",
          retryable: true,
          correlation_id: "cid_test",
        }),
      });
    await page.route("**/api/v1/models", failModels);
    await page.goto("/settings");
    await page.getByText("Avanzate · profili e modelli").click();
    const models = page.getByRole("region", { name: "Modelli" });
    await expect(models.getByRole("alert")).toBeVisible();
    await expect(models.getByRole("alert")).toContainText("cid_test");
    await expect(models.getByRole("button", { name: "Copia" })).toBeVisible();
    await page.unroute("**/api/v1/models", failModels);
    await models.getByRole("button", { name: "Riprova" }).click();
    await expect(models.getByText("echo", { exact: true })).toBeVisible();
  });

  test("readiness distingue runtime irraggiungibile da modello non qualificato", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      chat: {
        ...session.snapshot().chat,
        readiness: {
          state: "unreachable",
          model_name: "newray-gemma4-31b-it:ud-q4-k-xl",
          digest: null,
          declared_capabilities: [],
        },
      },
    });
    await page.goto("/settings");
    await page.getByText("Avanzate · profili e modelli").click();
    const notice = page.getByRole("region", { name: "Stato dell'Assistente" });
    await expect(notice).toContainText("Modello verificato: newray-gemma4-31b-it:ud-q4-k-xl");
    await expect(notice).toContainText("Ollama non è raggiungibile");
    session.snapshot().chat.readiness = {
      state: "installed_unverified",
      model_name: "newray-gemma4-31b-it:ud-q4-k-xl",
      digest: "sha256:test",
      declared_capabilities: ["completion", "tools"],
    };
    await page.reload();
    await page.getByText("Avanzate · profili e modelli").click();
    await expect(notice).toContainText("non sono ancora qualificate");
    await expect(notice).toContainText("completion, tools");
  });

  test("readiness rende tutti gli stati e le capacità qualificate future", async ({
    page,
    session,
  }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    const states = [
      ["not_configured", "non configurato"],
      ["unreachable", "non è raggiungibile"],
      ["runtime_error", "modo inatteso"],
      ["catalog_empty", "non ha modelli"],
      ["model_missing", "non è installato"],
      ["artifact_incompatible", "non dichiara"],
      ["installed_unverified", "non sono ancora qualificate"],
      ["available", "Modello qualificato"],
    ] as const;
    await page.goto("/settings");
    await page.getByText("Avanzate · profili e modelli").click();
    const notice = page.getByRole("region", { name: "Stato dell'Assistente" });
    for (const [state, expected] of states) {
      session.snapshot().chat.readiness = {
        state,
        model_name: "gemma-test",
        digest: "sha256:test",
        declared_capabilities: ["completion", "vision"],
        qualified_capabilities: state === "available" ? ["completion"] : [],
      };
      await page.reload();
      await page.getByText("Avanzate · profili e modelli").click();
      await expect(notice).toContainText(expected);
      if (state === "available") {
        await expect(notice).toContainText("Capacità qualificate: completion");
      }
    }
  });

  test("cronologia messaggi paginata senza mescolare conversazioni", async ({ page, session }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page).toHaveURL(/\/conversations\/conv_\d+$/);
    const firstId = page.url().split("/").at(-1)!;
    session.snapshot().chat.messages[firstId] = Array.from({ length: 201 }, (_, index) => ({
      id: `message_${index + 1}`,
      role: "assistant" as const,
      content: `Message ${index + 1}`,
      sequence: index + 1,
      created_at: "2026-09-24T00:00:00Z",
    }));
    await page.reload();
    await expect(page.locator("[data-role='assistant']")).toHaveCount(200);
    await page.getByRole("button", { name: "Carica altri messaggi" }).click();
    await expect(page.locator("[data-role='assistant']")).toHaveCount(201);
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page.locator("[data-role='assistant']")).toHaveCount(0);
  });

  test("cambio conversazione durante run: la risposta tardiva non appare nella nuova chat", async ({
    page,
    session,
  }) => {
    session.set({ me: { kind: "ok", identity: defaultIdentity } });
    let releaseRun: (() => void) | undefined;
    let startRun: (() => void) | undefined;
    let finishRun: (() => void) | undefined;
    const runStarted = new Promise<void>((resolve) => {
      startRun = resolve;
    });
    const runFinished = new Promise<void>((resolve) => {
      finishRun = resolve;
    });
    await page.route("**/api/v1/conversations/*/runs", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      const now = new Date().toISOString();
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "run_stale",
          conversation_id: "conv_stale",
          state: "queued",
          partial_text: "",
          finish_reason: null,
          prompt_tokens: null,
          completion_tokens: null,
          eval_duration_ns: null,
          created_at: now,
          updated_at: now,
        }),
      });
    });
    await page.route("**/api/v1/runs/*/events*", async (route) => {
      startRun?.();
      await new Promise<void>((release) => {
        releaseRun = release;
      });
      await route
        .fulfill({
          status: 200,
          contentType: "text/event-stream",
          body:
            'id: 1\nevent: message.delta\ndata: {"text":"RISPOSTA_VECCHIA"}\n\n' +
            'id: 2\nevent: run.completed\ndata: {"finish_reason":"stop","text":"RISPOSTA_VECCHIA"}\n\n',
        })
        .catch(() => {});
      finishRun?.();
    });
    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page).toHaveURL(/\/conversations\/conv_\d+$/);
    await page.getByLabel("Scrivi un messaggio…").fill("Prompt A");
    await page.getByRole("button", { name: "Invia" }).click();
    await runStarted;
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page.locator("[data-role='assistant']")).toHaveCount(0);
    releaseRun?.();
    await runFinished;
    await expect(page.locator("[data-role='assistant']")).toHaveCount(0);
  });

  test("logout durante run: stream chiuso e cache privata non passa al nuovo principal", async ({
    page,
    session,
  }) => {
    session.set({
      me: { kind: "ok", identity: defaultIdentity },
      status: { kind: "bootstrapped" },
    });
    let releaseRun: (() => void) | undefined;
    let startRun: (() => void) | undefined;
    const runStarted = new Promise<void>((resolve) => {
      startRun = resolve;
    });
    await page.route("**/api/v1/conversations/*/runs", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      const now = new Date().toISOString();
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "run_secret",
          conversation_id: "conv_secret",
          state: "queued",
          partial_text: "",
          finish_reason: null,
          prompt_tokens: null,
          completion_tokens: null,
          eval_duration_ns: null,
          created_at: now,
          updated_at: now,
        }),
      });
    });
    await page.route("**/api/v1/runs/*/events*", async (route) => {
      startRun?.();
      await new Promise<void>((release) => {
        releaseRun = release;
      });
      await route
        .fulfill({
          status: 200,
          contentType: "text/event-stream",
          body:
            'id: 1\nevent: message.delta\ndata: {"text":"SEGRETO_A"}\n\n' +
            'id: 2\nevent: run.completed\ndata: {"finish_reason":"stop","text":"SEGRETO_A"}\n\n',
        })
        .catch(() => {});
    });
    await page.goto("/conversations");
    await page.getByRole("button", { name: "Nuova conversazione" }).click();
    await expect(page).toHaveURL(/\/conversations\/conv_\d+$/);
    await page.getByLabel("Scrivi un messaggio…").fill("Prompt privato");
    await page.getByRole("button", { name: "Invia" }).click();
    await runStarted;
    await page.getByRole("link", { name: "Impostazioni" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    session.set({ me: { kind: "unauthenticated" } });
    await page.getByRole("button", { name: "Termina la sessione" }).click();
    await expect(page).toHaveURL(/\/login$/);
    releaseRun?.();

    const otherIdentity = {
      ...defaultIdentity,
      user_id: "usr_other",
      session_id: "sess_other",
      display_name: "Altro",
    };
    session.set({
      me: { kind: "ok", identity: otherIdentity },
      login: { kind: "ok", identity: otherIdentity },
      chat: {
        conversations: [],
        messages: {},
        profiles: session.snapshot().chat.profiles,
        run: { kind: "echo" },
      },
    });
    await page.getByLabel("Nome da mostrare").fill("Altro");
    await page.getByLabel("Password locale").fill("test-passphrase-1234");
    await page.getByRole("button", { name: "Accedi" }).click();
    await expect(page).toHaveURL(/\/conversations$/);
    await expect(page.getByText("Nessuna conversazione. Creane una per iniziare.")).toBeVisible();
    await expect(page.getByText("SEGRETO_A")).toHaveCount(0);
  });
});
