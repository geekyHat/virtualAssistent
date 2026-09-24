/**
 * Client API condiviso (NewRay.md §19.4).
 *
 * I cookie sono same-origin e HttpOnly (li gestisce il server); gli errori
 * arrivano con codice stabile, possibilità di retry e correlation ID. Il
 * browser non autorizza: il server decide. Niente token in localStorage.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  readonly correlationId: string;

  constructor(
    status: number,
    code: string,
    message: string,
    retryable: boolean,
    correlationId: string
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.correlationId = correlationId;
  }
}

interface ErrorPayload {
  code: string;
  message: string;
  retryable: boolean;
  correlation_id: string;
}

/** Soglia dichiarata (B-03.2-26): oltre questo tempo una richiesta appesa
 * fallisce esplicitamente invece di restare in sospeso indefinitamente. */
export const DEFAULT_TIMEOUT_MS = 15_000;

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<T> {
  const timeoutSignal = AbortSignal.timeout(timeoutMs);
  const signal = init.signal ? AbortSignal.any([init.signal, timeoutSignal]) : timeoutSignal;
  let response: Response;
  try {
    response = await fetch(path, { ...init, signal, credentials: "same-origin" });
  } catch (err) {
    // Timeout nostro (mai il segnale del chiamante, es. Stop): errore
    // esplicito e recuperabile, mai una sospensione silenziosa.
    if (timeoutSignal.aborted && !init.signal?.aborted) {
      throw new ApiError(0, "TIMEOUT", "la richiesta ha superato il tempo massimo", true, "");
    }
    throw err;
  }
  if (response.ok) {
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
  let payload: ErrorPayload | null;
  try {
    payload = (await response.json()) as ErrorPayload;
  } catch {
    payload = null; // corpo non JSON: si degrada al codice stabile
  }
  throw new ApiError(
    response.status,
    payload?.code ?? "INTERNAL",
    payload?.message ?? "errore interno del server",
    payload?.retryable ?? false,
    payload?.correlation_id ?? ""
  );
}
