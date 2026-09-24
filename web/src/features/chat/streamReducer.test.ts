import { describe, expect, it } from "vitest";
import {
  formatTokenRate,
  initialStreamState,
  streamReducer,
  type StreamState,
} from "./streamReducer";

function run(events: Parameters<typeof streamReducer>[1][]): StreamState {
  return events.reduce(streamReducer, initialStreamState);
}

describe("streamReducer", () => {
  it("start → streaming; delta accumula il testo", () => {
    const state = run([
      { kind: "start" },
      { kind: "delta", text: "Ciao " },
      { kind: "delta", text: "mondo" },
    ]);
    expect(state.status).toBe("streaming");
    expect(state.text).toBe("Ciao mondo");
  });

  it("done con testo → completed con metrica autorevole", () => {
    const state = run([
      { kind: "start" },
      { kind: "delta", text: "ok" },
      {
        kind: "done",
        finishReason: "stop",
        tokensPerSecond: 42.5,
        promptTokens: 10,
        completionTokens: 20,
        model: "echo",
        digest: "sha256:echo000",
      },
    ]);
    expect(state.status).toBe("completed");
    expect(state.tokensPerSecond).toBe(42.5);
    expect(state.model).toBe("echo");
    expect(state.digest).toBe("sha256:echo000");
  });

  it("done finish_reason length → truncated, non stop", () => {
    const state = run([
      { kind: "start" },
      { kind: "delta", text: "parziale" },
      {
        kind: "done",
        finishReason: "length",
        tokensPerSecond: null,
        promptTokens: null,
        completionTokens: null,
        model: "echo",
        digest: null,
      },
    ]);
    expect(state.status).toBe("truncated");
    expect(state.text).toBe("parziale");
  });

  it("done finish_reason cancelled → cancelled (Stop dal server)", () => {
    const state = run([
      { kind: "start" },
      { kind: "delta", text: "frammento" },
      {
        kind: "done",
        finishReason: "cancelled",
        tokensPerSecond: null,
        promptTokens: null,
        completionTokens: null,
        model: null,
        digest: null,
      },
    ]);
    expect(state.status).toBe("cancelled");
    expect(state.text).toBe("frammento");
  });

  it("done senza testo → terminale vuoto, non completion", () => {
    const state = run([
      { kind: "start" },
      {
        kind: "done",
        finishReason: "stop",
        tokensPerSecond: null,
        promptTokens: null,
        completionTokens: null,
        model: "echo",
        digest: null,
      },
    ]);
    expect(state.status).toBe("empty");
  });

  it("error dopo delta → failed, testo parziale conservato", () => {
    const state = run([
      { kind: "start" },
      { kind: "delta", text: "testo parziale " },
      { kind: "error", message: "errore del modello" },
    ]);
    expect(state.status).toBe("failed");
    expect(state.error).toBe("errore del modello");
    expect(state.text).toBe("testo parziale ");
  });

  it("error prima di qualsiasi delta → failed con testo vuoto", () => {
    const state = run([{ kind: "start" }, { kind: "error", message: "boom" }]);
    expect(state.status).toBe("failed");
    expect(state.text).toBe("");
  });

  it("EOF senza terminale → eof, mai falsa completion", () => {
    const state = run([{ kind: "start" }, { kind: "delta", text: "parziale" }, { kind: "eof" }]);
    expect(state.status).toBe("eof");
    expect(state.text).toBe("parziale");
  });

  it("abort locale → aborted; il client non dichiara un terminale autorevole", () => {
    const state = run([
      { kind: "start" },
      { kind: "delta", text: "frammento" },
      { kind: "aborted" },
    ]);
    expect(state.status).toBe("aborted");
    expect(state.tokensPerSecond).toBeNull();
  });

  it("terminale unico: un terminale non viene sovrascritto", () => {
    const state = run([
      { kind: "start" },
      { kind: "delta", text: "ok" },
      {
        kind: "done",
        finishReason: "stop",
        tokensPerSecond: 10,
        promptTokens: null,
        completionTokens: null,
        model: "echo",
        digest: null,
      },
      { kind: "error", message: "dopo terminale" },
      { kind: "delta", text: "dopo terminale" },
    ]);
    expect(state.status).toBe("completed");
    expect(state.error).toBeNull();
    expect(state.text).toBe("ok");
  });

  it("delta prima di start è un no-op", () => {
    const state = run([{ kind: "delta", text: "orfano" }]);
    expect(state.status).toBe("idle");
    expect(state.text).toBe("");
  });

  it("formatTokenRate: misura in corso, valore con unità, non disponibile (mai zero inventato)", () => {
    expect(formatTokenRate(null, true)).toBe("misura in corso…");
    expect(formatTokenRate(42.5, false)).toBe("42.5 token/s");
    expect(formatTokenRate(null, false)).toBe("non disponibile");
  });
});
