import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { SseDecoder, type SseFrame } from "../../shared/api/sse";
import {
  formatTokenRate,
  initialRunEventState,
  runEventsReducer,
  type RunEventAction,
  type RunEventStatus,
} from "./runEventsReducer";

export interface ChatState {
  /** `true` finché il run non ha un esito terminale. */
  streaming: boolean;
  status: RunEventStatus;
  text: string;
  error: string | null;
  correlationId: string | null;
  tokensPerSecond: number | null;
  promptTokens: number | null;
  completionTokens: number | null;
  /** Richiesta di stop inviata, esito autorevole non ancora arrivato. */
  cancelRequested: boolean;
  /** Meter «token/s» in forma pronta da mostrare. */
  tokenRate: string;
}

const TERMINAL_EVENT_TYPES = new Set([
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.interrupted",
]);
const TERMINAL_RUN_STATES = new Set(["completed", "failed", "cancelled", "interrupted"]);

export function useChat(conversationId: string | undefined) {
  const [state, dispatch] = useReducer(runEventsReducer, initialRunEventState);
  const [streaming, setStreaming] = useState(false);
  const [streamOwnerId, setStreamOwnerId] = useState<string | undefined>();
  const abortRef = useRef<AbortController | null>(null);
  const runIdRef = useRef<string | null>(null);

  // Chiusura dello stream all'unmount (B-03.2-26): navigare via o
  // disconnettersi durante uno streaming non deve lasciare la richiesta
  // aperta né una risposta tardiva scartata solo per fortuna.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  // La route cambia senza smontare ConversationsPage. Un delta della
  // conversazione precedente non deve comparire sotto quella nuova.
  useEffect(() => {
    if (streamOwnerId !== undefined && streamOwnerId !== conversationId) {
      abortRef.current?.abort();
      abortRef.current = null;
      runIdRef.current = null;
      setStreaming(false);
      setStreamOwnerId(undefined);
      dispatch({ kind: "reset" });
    }
  }, [conversationId, streamOwnerId]);

  const subscribeEvents = useCallback(
    async (
      runId: string,
      controller: AbortController,
      afterSequence: number,
      allowReconnect: boolean
    ): Promise<void> => {
      let lastSequence = afterSequence;
      let sawTerminal = false;

      try {
        const response = await fetch(
          `/api/v1/runs/${runId}/events?after_sequence=${afterSequence}`,
          { credentials: "same-origin", signal: controller.signal }
        );
        if (abortRef.current !== controller) return;

        if (!response.ok || !response.body) {
          dispatch({ kind: "transportError", message: `HTTP ${response.status}` });
          setStreaming(false);
          return;
        }

        const reader = response.body.getReader();
        const textDecoder = new TextDecoder();
        const sse = new SseDecoder();

        while (true) {
          const { done, value } = await reader.read();
          if (abortRef.current !== controller) return;
          if (done) break;
          for (const frame of sse.push(textDecoder.decode(value, { stream: true }))) {
            if (applyFrame(dispatch, frame, (seq) => (lastSequence = seq))) sawTerminal = true;
          }
        }
        for (const frame of sse.finish()) {
          if (applyFrame(dispatch, frame, (seq) => (lastSequence = seq))) sawTerminal = true;
        }
      } catch (err) {
        if (abortRef.current !== controller) return;
        if ((err as Error).name !== "AbortError") {
          dispatch({ kind: "transportError", message: (err as Error).message });
        }
        setStreaming(false);
        return;
      }

      if (abortRef.current !== controller) return;

      if (sawTerminal) {
        setStreaming(false);
        return;
      }

      if (allowReconnect) {
        // Un solo tentativo di riconnessione dall'ultima sequenza applicata
        // (politica iniziale dichiarata, non un backoff generale).
        await subscribeEvents(runId, controller, lastSequence, false);
        return;
      }

      // EOF senza terminale dopo il tentativo di riconnessione: guasto
      // visibile, mai una falsa completion (NewRay.md §19.4).
      dispatch({ kind: "transportError", message: "stream degli eventi interrotto" });
      setStreaming(false);
    },
    []
  );

  // Resume (P-06): una scheda nuova o un refresh a metà generazione non
  // conoscono ancora un run_id. Al mount/cambio conversazione si chiede al
  // server se esiste un run non terminale per riprenderne lo stream dalla
  // sequenza 0 (replay completo, stesso percorso di `send`). Non dipende
  // da `streamOwnerId`: quello cambierebbe per effetto di QUESTO stesso
  // effect (adottando il run), e ririeseguirlo per quello ricreerebbe il
  // controller appena assegnato e abortirebbe la sottoscrizione che ha
  // appena avviato. La guardia contro una `send()` concorrente è
  // `abortRef.current`, un ref sempre aggiornato, non lo stato chiuso
  // nella closure di questo effect.
  useEffect(() => {
    if (!conversationId) return;
    const controller = new AbortController();
    let disposed = false;

    void (async () => {
      let run: { id: string } | null;
      try {
        const response = await fetch(`/api/v1/conversations/${conversationId}/active-run`, {
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (disposed || !response.ok) return;
        run = (await response.json()) as { id: string } | null;
      } catch {
        // Nessun retry: se il resume fallisce, restano la cronologia
        // persistita e la possibilità di inviare un nuovo messaggio.
        return;
      }
      if (disposed || run === null) return;
      // Una `send()` esplicita ha già reclamato il trasporto nel
      // frattempo: il resume non la sovrascrive (mai due run concorrenti
      // sullo stesso hook).
      if (abortRef.current !== null) return;

      abortRef.current = controller;
      runIdRef.current = run.id;
      setStreamOwnerId(conversationId);
      dispatch({ kind: "start" });
      setStreaming(true);

      await subscribeEvents(run.id, controller, 0, true);
      if (abortRef.current === controller) abortRef.current = null;
    })();

    return () => {
      disposed = true;
      controller.abort();
    };
  }, [conversationId, subscribeEvents]);

  const send = useCallback(
    async (conversationId: string, profileId: string, content: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      runIdRef.current = null;
      setStreamOwnerId(conversationId);

      dispatch({ kind: "start" });
      setStreaming(true);

      try {
        const response = await fetch(`/api/v1/conversations/${conversationId}/runs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({
            content,
            profile_id: profileId,
            idempotency_key: crypto.randomUUID(),
          }),
          signal: controller.signal,
        });
        if (abortRef.current !== controller) return;

        if (!response.ok) {
          const body = await response.json().catch(() => null);
          if (abortRef.current !== controller) return;
          dispatch({
            kind: "transportError",
            message: (body as { message?: string } | null)?.message ?? `HTTP ${response.status}`,
            correlationId: (body as { correlation_id?: string } | null)?.correlation_id,
          });
          setStreaming(false);
          return;
        }

        const run = (await response.json()) as { id: string };
        if (abortRef.current !== controller) return;
        runIdRef.current = run.id;

        await subscribeEvents(run.id, controller, 0, true);
      } catch (err) {
        if (abortRef.current !== controller) return;
        if ((err as Error).name !== "AbortError") {
          dispatch({ kind: "transportError", message: (err as Error).message });
        }
        setStreaming(false);
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [subscribeEvents]
  );

  const stop = useCallback(() => {
    const runId = runIdRef.current;
    if (!runId) return;
    // Bandiera locale ottimistica; l'esito autorevole arriva dal
    // terminale sullo stream già aperto (non abortiamo il trasporto qui).
    dispatch({ kind: "cancelRequested" });
    void fetch(`/api/v1/runs/${runId}/cancel`, {
      method: "POST",
      credentials: "same-origin",
    }).catch(() => {
      // Nessun retry cieco (NewRay.md §19.4): l'esito incerto resta
      // incerto; lo stream in corso porterà comunque l'esito reale.
    });
  }, []);

  const visible = streamOwnerId === conversationId ? state : initialRunEventState;
  const visibleStreaming = streamOwnerId === conversationId && streaming;

  return {
    ...visible,
    streaming: visibleStreaming,
    tokenRate: formatTokenRate(visible.tokensPerSecond, visibleStreaming),
    send,
    stop,
  };
}

/**
 * Traduce un frame SSE grezzo in un'azione del reducer; ritorna `true` se il
 * frame è (o porta a) un esito terminale, per fermare il poll del chiamante.
 * Un `id:` mancante o non numerico su un evento diverso da `error` è di per
 * sé un guasto (il contratto lo garantisce sempre): niente sequenza
 * inventata che romperebbe il dedup del reducer.
 */
function applyFrame(
  dispatch: React.Dispatch<RunEventAction>,
  frame: SseFrame,
  onSequence: (sequence: number) => void
): boolean {
  if (frame.event === "error") {
    let parsed: Record<string, unknown> = {};
    try {
      parsed = JSON.parse(frame.data) as Record<string, unknown>;
    } catch {
      // Corpo malformato: il messaggio resta generico, non un blocco.
    }
    dispatch({
      kind: "transportError",
      message: typeof parsed.message === "string" ? parsed.message : "errore del server",
    });
    return true;
  }

  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(frame.data) as Record<string, unknown>;
  } catch {
    dispatch({ kind: "transportError", message: "frame SSE non valido" });
    return true;
  }

  if (frame.event === "resync") {
    if (frame.id === null) {
      dispatch({ kind: "transportError", message: "frame resync senza id" });
      return true;
    }
    onSequence(frame.id);
    dispatch({ kind: "resync", sequence: frame.id, payload: parsed });
    const runState = typeof parsed.state === "string" ? parsed.state : "";
    return TERMINAL_RUN_STATES.has(runState);
  }

  if (frame.id === null) {
    dispatch({ kind: "transportError", message: `evento ${frame.event} senza id` });
    return true;
  }
  onSequence(frame.id);
  dispatch({ kind: "event", type: frame.event, sequence: frame.id, payload: parsed });
  return TERMINAL_EVENT_TYPES.has(frame.event);
}
