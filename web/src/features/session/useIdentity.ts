import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "../../shared/api/client";
import type { Identity } from "./types";

/** Chiave cache identità: scoped al contesto utente (web AGENTS). */
export const IDENTITY_QUERY_KEY = ["identity"] as const;

export type SessionStatus = "loading" | "signed-in" | "signed-out" | "error";

export interface IdentityState {
  status: SessionStatus;
  identity: Identity | null;
  message: string | null;
  refetch: () => void;
}

/**
 * Risolve la sessione corrente dal server (GET /api/v1/me).
 * Qualsiasi 401 (UNAUTHENTICATED o SESSION_INVALID, p.es. cookie già
 * revocato ma ancora presente) significa "non autenticato", non un
 * errore: è lo stato da cui parte il bootstrap (NewRay.md §18.3).
 */
export function useIdentity(): IdentityState {
  const query = useQuery({
    queryKey: IDENTITY_QUERY_KEY,
    queryFn: () => apiFetch<Identity>("/api/v1/me"),
  });

  if (query.isPending) {
    return {
      status: "loading",
      identity: null,
      message: null,
      refetch: () => void query.refetch(),
    };
  }
  if (query.isSuccess) {
    return {
      status: "signed-in",
      identity: query.data,
      message: null,
      refetch: () => void query.refetch(),
    };
  }
  const error = query.error;
  if (error instanceof ApiError && error.status === 401) {
    return {
      status: "signed-out",
      identity: null,
      message: null,
      refetch: () => void query.refetch(),
    };
  }
  return {
    status: "error",
    identity: null,
    message: error instanceof ApiError ? error.message : "errore interno del server",
    refetch: () => void query.refetch(),
  };
}
