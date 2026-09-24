/**
 * Reducer dello stream di generazione (B-03.2-35).
 *
 * Puro e deterministico: riceve frame SSE validati e produce lo stato
 * della risposta corrente. Stati terminali distinti e terminale unico:
 *
 * - `done.finish_reason = "stop"`      → completata
 * - `done.finish_reason = "length"`    → troncata (non `stop`)
 * - `done.finish_reason = "cancelled"` → interrotta dal server (Stop)
 * - `done` con testo vuoto            → terminale vuoto (nessuna risposta)
 * - `error` (anche dopo delta)        → fallita; il testo parziale resta
 * - EOF senza terminale               → `eof` (guasto, non completion)
 * - abort locale                       → `aborted` (arresto richiesto)
 *
 * La metrica token/s viene presa esclusivamente dal terminale `done`
 * (misura autorevole del runtime, B-03.2-34); durante lo stream il meter
 * indica solo che la misura è in corso. Nessuna stima dal testo o dal
 * wall clock del browser.
 */

export type StreamStatus =
  | "idle"
  | "streaming"
  | "completed"
  | "truncated"
  | "cancelled"
  | "empty"
  | "failed"
  | "eof"
  | "aborted";

export interface StreamState {
  status: StreamStatus;
  text: string;
  /** Errore del server (evento `error` o HTTP non 2xx). */
  error: string | null;
  correlationId: string | null;
  /** Misura autorevole del runtime; `null` = non disponibile. */
  tokensPerSecond: number | null;
  promptTokens: number | null;
  completionTokens: number | null;
  /** Modello effettivo dal terminale del run (non il selettore). */
  model: string | null;
  digest: string | null;
}

export const initialStreamState: StreamState = {
  status: "idle",
  text: "",
  error: null,
  correlationId: null,
  tokensPerSecond: null,
  promptTokens: null,
  completionTokens: null,
  model: null,
  digest: null,
};

export type StreamEvent =
  | { kind: "start" }
  | { kind: "reset" }
  | { kind: "delta"; text: string }
  | {
      kind: "done";
      finishReason: string;
      tokensPerSecond: number | null;
      promptTokens: number | null;
      completionTokens: number | null;
      model: string | null;
      digest: string | null;
    }
  | { kind: "error"; message: string; correlationId?: string | null }
  | { kind: "eof" }
  | { kind: "aborted" };

export function streamReducer(state: StreamState, event: StreamEvent): StreamState {
  if (event.kind === "reset") return initialStreamState;
  // Terminale unico: un terminale già raggiunto non viene sovrascritto.
  if (state.status !== "idle" && state.status !== "streaming" && event.kind !== "start") {
    return state;
  }

  switch (event.kind) {
    case "start":
      return { ...initialStreamState, status: "streaming" };

    case "delta":
      if (state.status !== "streaming") return state;
      return { ...state, text: state.text + event.text };

    case "done": {
      if (state.status !== "streaming") return state;
      const status: StreamStatus =
        event.finishReason === "length"
          ? "truncated"
          : event.finishReason === "cancelled"
            ? "cancelled"
            : state.text.length === 0
              ? "empty"
              : "completed";
      return {
        ...state,
        status,
        tokensPerSecond: event.tokensPerSecond,
        promptTokens: event.promptTokens,
        completionTokens: event.completionTokens,
        model: event.model,
        digest: event.digest,
      };
    }

    case "error":
      // Conserva il testo parziale; lo stato è fallita, non completion.
      return {
        ...state,
        status: "failed",
        error: event.message,
        correlationId: event.correlationId ?? null,
      };

    case "eof":
      // EOF senza terminale: guasto visibile, mai una falsa completion.
      return { ...state, status: "eof" };

    case "aborted":
      // Arresto richiesto dal client: il server decide l'esito finale
      // (il backend salva il frammento con finish_reason "cancelled"),
      // ma il client non dichiara un terminale autorevole da solo.
      return { ...state, status: "aborted" };
  }
}

/**
 * Formatta il meter «token/s»: valore autorevole con unità, o lo stato
 * non disponibile. `null` mai reso come zero.
 */
export function formatTokenRate(tokensPerSecond: number | null, measuring: boolean): string {
  if (measuring) return "misura in corso…";
  if (tokensPerSecond === null) return "non disponibile";
  return `${tokensPerSecond.toFixed(1)} token/s`;
}
