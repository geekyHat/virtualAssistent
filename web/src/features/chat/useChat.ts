import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { SseDecoder } from "../../shared/api/sse";
import {
  formatTokenRate,
  initialStreamState,
  streamReducer,
  type StreamEvent,
  type StreamStatus,
} from "./streamReducer";

export interface ChatState {
  /** `true` finché il run non ha un esito terminale. */
  streaming: boolean;
  status: StreamStatus;
  text: string;
  error: string | null;
  correlationId: string | null;
  tokensPerSecond: number | null;
  promptTokens: number | null;
  completionTokens: number | null;
  model: string | null;
  digest: string | null;
  /** Meter «token/s» in forma pronta da mostrare. */
  tokenRate: string;
}

export function useChat(conversationId: string | undefined) {
  const [state, dispatch] = useReducer(streamReducer, initialStreamState);
  const [streaming, setStreaming] = useState(false);
  const [streamOwnerId, setStreamOwnerId] = useState<string | undefined>();
  const abortRef = useRef<AbortController | null>(null);

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
      setStreaming(false);
      setStreamOwnerId(undefined);
      dispatch({ kind: "reset" });
    }
  }, [conversationId, streamOwnerId]);

  const send = useCallback(async (conversationId: string, profileId: string, content: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setStreamOwnerId(conversationId);

    dispatch({ kind: "start" });
    setStreaming(true);

    try {
      const response = await fetch(`/api/v1/conversations/${conversationId}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ content, profile_id: profileId }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => null);
        if (abortRef.current !== controller) return;
        dispatch({
          kind: "error",
          message: (body as { message?: string } | null)?.message ?? `HTTP ${response.status}`,
          correlationId: (body as { correlation_id?: string } | null)?.correlation_id,
        });
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
          applyFrame(dispatch, frame);
        }
      }

      for (const frame of sse.finish()) {
        applyFrame(dispatch, frame);
      }

      // Il backend termina sempre con `done`; se lo stream si chiude
      // senza terminale è un guasto, non una completion. Il reducer
      // rende no-op l'`eof` se un terminale è già arrivato.
      dispatch({ kind: "eof" });
      setStreaming(false);
    } catch (err) {
      if (abortRef.current !== controller) return;
      if ((err as Error).name === "AbortError") {
        // Il server decide l'esito finale (salva il frammento con
        // finish_reason "cancelled"); il client non dichiara un
        // terminale autorevole da solo.
        dispatch({ kind: "aborted" });
      } else {
        dispatch({ kind: "error", message: (err as Error).message });
      }
      setStreaming(false);
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const visible = streamOwnerId === conversationId ? state : initialStreamState;
  const visibleStreaming = streamOwnerId === conversationId && streaming;

  return {
    ...visible,
    streaming: visibleStreaming,
    tokenRate: formatTokenRate(visible.tokensPerSecond, visibleStreaming),
    send,
    stop,
  };
}

function applyFrame(dispatch: React.Dispatch<StreamEvent>, frame: { event: string; data: string }) {
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(frame.data) as Record<string, unknown>;
  } catch {
    // Frame malformato: nessun retry cieco (NewRay.md §19.4);
    // errore esplicito.
    dispatch({ kind: "error", message: "frame SSE non valido" });
    return;
  }

  switch (frame.event) {
    case "delta":
      dispatch({ kind: "delta", text: String(parsed.text ?? "") });
      break;
    case "done":
      dispatch({
        kind: "done",
        finishReason: String(parsed.finish_reason ?? ""),
        tokensPerSecond:
          typeof parsed.tokens_per_second === "number" ? parsed.tokens_per_second : null,
        promptTokens: typeof parsed.prompt_tokens === "number" ? parsed.prompt_tokens : null,
        completionTokens:
          typeof parsed.completion_tokens === "number" ? parsed.completion_tokens : null,
        model: parsed.model != null ? String(parsed.model) : null,
        digest: parsed.digest != null ? String(parsed.digest) : null,
      });
      break;
    case "error":
      dispatch({
        kind: "error",
        message: String(parsed.message ?? "errore del modello"),
        correlationId: typeof parsed.correlation_id === "string" ? parsed.correlation_id : null,
      });
      break;
    default:
      // Eventi non previsti dal contratto: no-op esplicito.
      break;
  }
}
