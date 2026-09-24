/**
 * Reducer degli eventi durevoli del run (P-06, NewRay.md §19.3).
 *
 * Puro e deterministico, come `streamReducer` (che resta per la route
 * inline preservata, non più chiamata dalla UI — "senza doppio motore"
 * significa un solo reducer attivo, non che il vecchio sparisca).
 *
 * Differenze dal reducer inline:
 * - dedup per `sequence`: un evento già applicato (`sequence <=
 *   state.sequence`) è un no-op reale, non solo un guard sullo stato;
 * - `message.delta` porta il testo CUMULATIVO noto al checkpoint (non un
 *   frammento), quindi si sostituisce, non si concatena — l'idempotenza
 *   segue dalla sostituzione, non da un controllo aggiuntivo;
 * - un salto di sequenza non atteso (buco che il server non ha già
 *   risolto con `resync`) porta a `desync`: segnale per l'hook, che deve
 *   risottoscrivere, non qualcosa che il reducer puro può risolvere da solo;
 * - `resync` porta uno snapshot completo (stato/testo/finish_reason) e
 *   riallinea la baseline della sequenza, sostituendo qualunque interpolazione.
 */

export type RunEventStatus =
  | "idle"
  | "queued"
  | "streaming"
  | "completed"
  | "truncated"
  | "cancelled"
  | "interrupted"
  | "empty"
  | "failed"
  | "desync";

export interface RunEventState {
  status: RunEventStatus;
  text: string;
  /** Ultima sequenza applicata; 0 = nessun evento ancora ricevuto. */
  sequence: number;
  error: string | null;
  correlationId: string | null;
  /** Richiesta di stop inviata al server, esito non ancora arrivato. */
  cancelRequested: boolean;
  /** Misura autorevole del runtime, dal solo terminale (B-03.2-34). */
  tokensPerSecond: number | null;
  promptTokens: number | null;
  completionTokens: number | null;
}

export const initialRunEventState: RunEventState = {
  status: "idle",
  text: "",
  sequence: 0,
  error: null,
  correlationId: null,
  cancelRequested: false,
  tokensPerSecond: null,
  promptTokens: null,
  completionTokens: null,
};

interface TerminalPayload {
  finish_reason?: unknown;
  text?: unknown;
  prompt_tokens?: unknown;
  completion_tokens?: unknown;
  eval_duration_ns?: unknown;
}

interface ResyncPayload {
  state?: unknown;
  partial_text?: unknown;
  finish_reason?: unknown;
  prompt_tokens?: unknown;
  completion_tokens?: unknown;
  eval_duration_ns?: unknown;
}

export type RunEventAction =
  | { kind: "reset" }
  | { kind: "start" }
  | { kind: "cancelRequested" }
  | { kind: "event"; type: string; sequence: number; payload: Record<string, unknown> }
  | { kind: "resync"; sequence: number; payload: Record<string, unknown> }
  | { kind: "transportError"; message: string; correlationId?: string | null };

const TERMINAL_STATUSES: readonly RunEventStatus[] = [
  "completed",
  "truncated",
  "cancelled",
  "interrupted",
  "empty",
  "failed",
];

function isTerminal(status: RunEventStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

function tokensPerSecond(
  completionTokens: number | null,
  evalDurationNs: number | null
): number | null {
  if (completionTokens === null || evalDurationNs === null) return null;
  if (completionTokens <= 0 || evalDurationNs <= 0) return null;
  return Math.round((completionTokens / (evalDurationNs / 1_000_000_000)) * 100) / 100;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

// Alcuni tipi di evento/stato hanno un esito univoco a prescindere dal
// `finish_reason` (che per un fallimento del modello è "model_error" o
// "deadline_exceeded", non un valore che terminalStatusFor riconosce da
// solo): l'hint viene dal tipo di evento (diretto) o dallo `state` dello
// snapshot (resync), mai indovinato dal solo testo/finish_reason.
const EVENT_TYPE_STATUS_HINT: Readonly<Partial<Record<string, RunEventStatus>>> = {
  "run.failed": "failed",
  "run.cancelled": "cancelled",
  "run.interrupted": "interrupted",
};
const RUN_STATE_STATUS_HINT: Readonly<Partial<Record<string, RunEventStatus>>> = {
  failed: "failed",
  cancelled: "cancelled",
  interrupted: "interrupted",
};

function terminalStatusFor(
  hint: RunEventStatus | undefined,
  finishReason: string,
  text: string
): RunEventStatus {
  if (finishReason === "length") return "truncated";
  if (finishReason === "cancelled_by_user") return "cancelled";
  if (finishReason === "worker_lost") return "interrupted";
  if (hint) return hint;
  return text.length === 0 ? "empty" : "completed";
}

function applyTerminal(
  state: RunEventState,
  sequence: number,
  payload: TerminalPayload,
  hint: RunEventStatus | undefined
): RunEventState {
  const text = typeof payload.text === "string" ? payload.text : state.text;
  const finishReason = typeof payload.finish_reason === "string" ? payload.finish_reason : "";
  const promptTokens = numberOrNull(payload.prompt_tokens);
  const completionTokens = numberOrNull(payload.completion_tokens);
  const evalDurationNs = numberOrNull(payload.eval_duration_ns);
  return {
    ...state,
    status: terminalStatusFor(hint, finishReason, text),
    text,
    sequence,
    cancelRequested: false,
    promptTokens,
    completionTokens,
    tokensPerSecond: tokensPerSecond(completionTokens, evalDurationNs),
  };
}

export function runEventsReducer(state: RunEventState, action: RunEventAction): RunEventState {
  if (action.kind === "reset") return initialRunEventState;
  if (action.kind === "start") return { ...initialRunEventState, status: "queued" };
  if (action.kind === "cancelRequested") return { ...state, cancelRequested: true };

  // Terminale unico: un terminale già raggiunto non viene sovrascritto da
  // eventi arrivati dopo (replay/riconnessione non deve regredire lo stato).
  if (isTerminal(state.status)) return state;

  if (action.kind === "transportError") {
    return {
      ...state,
      status: "failed",
      error: action.message,
      correlationId: action.correlationId ?? null,
    };
  }

  if (action.kind === "resync") {
    const payload = action.payload as ResyncPayload;
    const text = typeof payload.partial_text === "string" ? payload.partial_text : state.text;
    const runState = typeof payload.state === "string" ? payload.state : "";
    if (runState === "queued") {
      return { ...state, status: "queued", text, sequence: action.sequence };
    }
    if (runState === "running") {
      return { ...state, status: "streaming", text, sequence: action.sequence };
    }
    // Stato terminale nello snapshot: stesso calcolo del terminale diretto.
    // Lo snapshot porta `partial_text`, non `text` (nomi di campo diversi
    // per lo stesso concetto tra evento diretto e resync): normalizzato
    // prima di riusare la stessa funzione, non duplicato.
    return applyTerminal(
      state,
      action.sequence,
      { ...payload, text } as TerminalPayload,
      RUN_STATE_STATUS_HINT[runState]
    );
  }

  // action.kind === "event"
  if (action.sequence <= state.sequence) return state; // dedup: già applicato
  if (state.sequence !== 0 && action.sequence !== state.sequence + 1) {
    // Buco non risolto da un resync del server: l'hook deve risottoscrivere,
    // il reducer si limita a segnalarlo senza inventare il contenuto mancante.
    return { ...state, status: "desync" };
  }

  switch (action.type) {
    case "run.queued":
      return { ...state, status: "queued", sequence: action.sequence };
    case "run.started":
      return { ...state, status: "streaming", sequence: action.sequence };
    case "message.delta": {
      const text = typeof action.payload.text === "string" ? action.payload.text : state.text;
      return { ...state, status: "streaming", text, sequence: action.sequence };
    }
    case "run.completed":
    case "run.failed":
    case "run.cancelled":
    case "run.interrupted":
      return applyTerminal(
        state,
        action.sequence,
        action.payload as TerminalPayload,
        EVENT_TYPE_STATUS_HINT[action.type]
      );
    default:
      // Tipo non previsto dal contratto (§19.3): no-op esplicito, non un errore.
      return { ...state, sequence: action.sequence };
  }
}

/** Formatta il meter «token/s»: valore autorevole con unità, mai una stima. */
export function formatTokenRate(tokensPerSecond: number | null, measuring: boolean): string {
  if (measuring) return "misura in corso…";
  if (tokensPerSecond === null) return "non disponibile";
  return `${tokensPerSecond.toFixed(1)} token/s`;
}
