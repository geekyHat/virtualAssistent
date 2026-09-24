import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../../shared/api/client";
import type { Identity } from "./types";
import { IDENTITY_QUERY_KEY } from "./useIdentity";
import { SESSION_STATUS_QUERY_KEY } from "./useSessionStatus";

export interface Credentials {
  displayName: string;
  credential: string;
}

/** Bootstrap monouso: a successo l'identità e lo stato installazione vengono rinfrescati. */
export function useBootstrap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Credentials) =>
      apiFetch<Identity>("/api/v1/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: payload.displayName,
          credential: payload.credential,
        }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: IDENTITY_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: SESSION_STATUS_QUERY_KEY });
    },
  });
}

/** Login del proprietario esistente (B-03.2-14). */
export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: Credentials) =>
      apiFetch<Identity>("/api/v1/session/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: payload.displayName,
          credential: payload.credential,
        }),
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: IDENTITY_QUERY_KEY });
    },
  });
}

/**
 * Revoca la sessione e svuota interamente la cache client (B-03.2-14).
 *
 * A differenza dell'invalidazione mirata sulle chiavi identity, `clear()`
 * elimina tutte le risposte cachate: se in futuro entreranno feature con
 * dati privati (conversazioni, documenti…), nessun frammento del
 * proprietario appena disconnesso resta a disposizione di un successivo
 * login su un'altra identità.
 */
export function useRevoke() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<void>("/api/v1/session/revoke", { method: "POST" }),
    onSuccess: async () => {
      // Le richieste in volo devono interrompersi prima di svuotare la
      // cache (B-03.2-26): altrimenti una risposta tardiva potrebbe
      // ripopolarla con dati del proprietario appena disconnesso.
      await queryClient.cancelQueries();
      queryClient.clear();
    },
  });
}
