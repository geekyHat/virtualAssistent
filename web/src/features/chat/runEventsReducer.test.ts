import { describe, expect, it } from "vitest";
import {
  formatTokenRate,
  initialRunEventState,
  runEventsReducer,
  type RunEventState,
} from "./runEventsReducer";

function run(actions: Parameters<typeof runEventsReducer>[1][]): RunEventState {
  return actions.reduce(runEventsReducer, initialRunEventState);
}

describe("runEventsReducer", () => {
  it("start → queued; run.started → streaming; message.delta sostituisce il testo cumulativo", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "run.queued", sequence: 1, payload: {} },
      { kind: "event", type: "run.started", sequence: 2, payload: {} },
      { kind: "event", type: "message.delta", sequence: 3, payload: { text: "Ciao" } },
      { kind: "event", type: "message.delta", sequence: 4, payload: { text: "Ciao mondo" } },
    ]);
    expect(state.status).toBe("streaming");
    expect(state.text).toBe("Ciao mondo");
    expect(state.sequence).toBe(4);
  });

  it("run.completed → completed con metrica autorevole dal solo terminale", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "run.queued", sequence: 1, payload: {} },
      { kind: "event", type: "run.started", sequence: 2, payload: {} },
      { kind: "event", type: "message.delta", sequence: 3, payload: { text: "ok" } },
      {
        kind: "event",
        type: "run.completed",
        sequence: 4,
        payload: {
          finish_reason: "stop",
          text: "ok",
          prompt_tokens: 10,
          completion_tokens: 20,
          eval_duration_ns: 1_000_000_000,
        },
      },
    ]);
    expect(state.status).toBe("completed");
    expect(state.text).toBe("ok");
    expect(state.tokensPerSecond).toBe(20);
    expect(state.promptTokens).toBe(10);
    expect(state.completionTokens).toBe(20);
  });

  it("finish_reason length → truncated", () => {
    const state = run([
      { kind: "start" },
      {
        kind: "event",
        type: "run.completed",
        sequence: 1,
        payload: { finish_reason: "length", text: "parziale" },
      },
    ]);
    expect(state.status).toBe("truncated");
  });

  it("finish_reason cancelled_by_user → cancelled", () => {
    const state = run([
      { kind: "start" },
      {
        kind: "event",
        type: "run.cancelled",
        sequence: 1,
        payload: { finish_reason: "cancelled_by_user", text: "frammento" },
      },
    ]);
    expect(state.status).toBe("cancelled");
    expect(state.text).toBe("frammento");
  });

  it("finish_reason worker_lost → interrupted", () => {
    const state = run([
      { kind: "start" },
      {
        kind: "event",
        type: "run.interrupted",
        sequence: 1,
        payload: { finish_reason: "worker_lost", text: "parziale" },
      },
    ]);
    expect(state.status).toBe("interrupted");
  });

  it("terminale con testo vuoto → empty", () => {
    const state = run([
      { kind: "start" },
      {
        kind: "event",
        type: "run.completed",
        sequence: 1,
        payload: { finish_reason: "stop", text: "" },
      },
    ]);
    expect(state.status).toBe("empty");
  });

  it("run.failed → failed", () => {
    const state = run([
      { kind: "start" },
      {
        kind: "event",
        type: "run.failed",
        sequence: 1,
        payload: { finish_reason: "error", text: "" },
      },
    ]);
    expect(state.status).toBe("failed");
  });

  it("dedup: un evento con sequence già applicata è un no-op reale", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "message.delta", sequence: 1, payload: { text: "a" } },
      { kind: "event", type: "message.delta", sequence: 1, payload: { text: "DUPLICATO" } },
    ]);
    expect(state.text).toBe("a");
    expect(state.sequence).toBe(1);
  });

  it("buco di sequenza non risolto dal server → desync, non contenuto inventato", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "message.delta", sequence: 1, payload: { text: "a" } },
      { kind: "event", type: "message.delta", sequence: 5, payload: { text: "salto" } },
    ]);
    expect(state.status).toBe("desync");
    expect(state.text).toBe("a");
  });

  it("resync riallinea la baseline sullo snapshot ricevuto (stato running)", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "message.delta", sequence: 1, payload: { text: "a" } },
      {
        kind: "resync",
        sequence: 50,
        payload: { state: "running", partial_text: "snapshot corrente" },
      },
    ]);
    expect(state.status).toBe("streaming");
    expect(state.text).toBe("snapshot corrente");
    expect(state.sequence).toBe(50);
  });

  it("resync con stato terminale nello snapshot → stesso calcolo del terminale diretto", () => {
    const state = run([
      { kind: "start" },
      {
        kind: "resync",
        sequence: 50,
        payload: {
          state: "completed",
          partial_text: "finale",
          finish_reason: "stop",
          prompt_tokens: 1,
          completion_tokens: 2,
          eval_duration_ns: 1_000_000_000,
        },
      },
    ]);
    expect(state.status).toBe("completed");
    expect(state.text).toBe("finale");
    expect(state.tokensPerSecond).toBe(2);
  });

  it("terminale unico: eventi successivi non regrediscono lo stato (replay/riconnessione)", () => {
    const state = run([
      { kind: "start" },
      {
        kind: "event",
        type: "run.completed",
        sequence: 1,
        payload: { finish_reason: "stop", text: "ok" },
      },
      { kind: "event", type: "message.delta", sequence: 2, payload: { text: "dopo terminale" } },
      {
        kind: "resync",
        sequence: 3,
        payload: { state: "running", partial_text: "dopo terminale" },
      },
    ]);
    expect(state.status).toBe("completed");
    expect(state.text).toBe("ok");
  });

  it("transportError → failed", () => {
    const state = run([
      { kind: "start" },
      { kind: "transportError", message: "stream interrotto", correlationId: "abc" },
    ]);
    expect(state.status).toBe("failed");
    expect(state.error).toBe("stream interrotto");
    expect(state.correlationId).toBe("abc");
  });

  it("cancelRequested: bandiera locale finché l'esito autorevole non arriva", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "run.started", sequence: 1, payload: {} },
      { kind: "cancelRequested" },
    ]);
    expect(state.cancelRequested).toBe(true);
    expect(state.status).toBe("streaming");
  });

  it("il terminale cancelled arriva e azzera la bandiera cancelRequested", () => {
    const state = run([
      { kind: "start" },
      { kind: "cancelRequested" },
      {
        kind: "event",
        type: "run.cancelled",
        sequence: 1,
        payload: { finish_reason: "cancelled_by_user", text: "parziale" },
      },
    ]);
    expect(state.cancelRequested).toBe(false);
    expect(state.status).toBe("cancelled");
  });

  it("tool.executing mostra il tool attivo; tool.succeeded lo libera, il run continua", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "run.started", sequence: 1, payload: {} },
      {
        kind: "event",
        type: "tool.executing",
        sequence: 2,
        payload: { tool_name: "run.status", is_error: false },
      },
    ]);
    expect(state.activeTool).toBe("run.status");
    expect(state.status).toBe("streaming");

    const after = runEventsReducer(state, {
      kind: "event",
      type: "tool.succeeded",
      sequence: 3,
      payload: { tool_name: "run.status", is_error: false },
    });
    expect(after.activeTool).toBeNull();
    expect(after.status).toBe("streaming");
  });

  it("tool.failed non è un guasto del run: libera il tool, la generazione prosegue", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "run.started", sequence: 1, payload: {} },
      { kind: "event", type: "tool.executing", sequence: 2, payload: { tool_name: "run.status" } },
      { kind: "event", type: "tool.failed", sequence: 3, payload: { tool_name: "run.status" } },
      {
        kind: "event",
        type: "run.completed",
        sequence: 4,
        payload: { finish_reason: "stop", text: "fatto" },
      },
    ]);
    expect(state.status).toBe("completed");
    expect(state.text).toBe("fatto");
    expect(state.activeTool).toBeNull();
  });

  it("il terminale azzera il tool attivo", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "tool.executing", sequence: 1, payload: { tool_name: "run.status" } },
      {
        kind: "event",
        type: "run.completed",
        sequence: 2,
        payload: { finish_reason: "stop", text: "ok" },
      },
    ]);
    expect(state.activeTool).toBeNull();
  });

  it("reset → stato iniziale", () => {
    const state = run([
      { kind: "start" },
      { kind: "event", type: "message.delta", sequence: 1, payload: { text: "a" } },
      { kind: "reset" },
    ]);
    expect(state).toEqual(initialRunEventState);
  });

  it("formatTokenRate: misura in corso, valore con unità, non disponibile (mai zero inventato)", () => {
    expect(formatTokenRate(null, true)).toBe("misura in corso…");
    expect(formatTokenRate(42.5, false)).toBe("42.5 token/s");
    expect(formatTokenRate(null, false)).toBe("non disponibile");
  });
});
