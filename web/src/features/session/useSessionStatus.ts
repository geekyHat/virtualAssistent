import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";

/**
 * Stato pubblico dell'installazione (B-03.2-14).
 *
 * La chiave è globale (non scoped a un'identità): la risposta non contiene
 * dati privati, solo la presenza o meno del proprietario. Serve a
 * decidere se la landing mostra bootstrap o rimanda a login, senza
 * tentare bootstrap e leggere il CONFLICT come segnale.
 */

export const SESSION_STATUS_QUERY_KEY = ["session-status"] as const;

interface SessionStatusPayload {
  bootstrapped: boolean;
}

export interface SessionStatusState {
  status: "loading" | "ready" | "error";
  bootstrapped: boolean | null;
  message: string | null;
  refetch: () => void;
}

export function useSessionStatus(): SessionStatusState {
  const query = useQuery({
    queryKey: SESSION_STATUS_QUERY_KEY,
    queryFn: () => apiFetch<SessionStatusPayload>("/api/v1/session/status"),
    staleTime: 0,
  });

  if (query.isPending) {
    return {
      status: "loading",
      bootstrapped: null,
      message: null,
      refetch: () => void query.refetch(),
    };
  }
  if (query.isSuccess) {
    return {
      status: "ready",
      bootstrapped: query.data.bootstrapped,
      message: null,
      refetch: () => void query.refetch(),
    };
  }
  return {
    status: "error",
    bootstrapped: null,
    message: query.error instanceof Error ? query.error.message : "errore",
    refetch: () => void query.refetch(),
  };
}
