import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiFetch", () => {
  it("restituisce il corpo JSON su risposta 2xx", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
    );
    await expect(apiFetch<{ ok: boolean }>("/api/v1/x")).resolves.toEqual({ ok: true });
  });

  it("restituisce undefined su 204", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 204 }))
    );
    await expect(apiFetch<void>("/api/v1/x")).resolves.toBeUndefined();
  });

  it("mappa un errore non-2xx in ApiError con i campi del payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              code: "CONFLICT",
              message: "già presente",
              retryable: false,
              correlation_id: "cid-1",
            }),
            { status: 409 }
          )
      )
    );
    await expect(apiFetch("/api/v1/x")).rejects.toMatchObject({
      status: 409,
      code: "CONFLICT",
      message: "già presente",
      correlationId: "cid-1",
    });
  });

  it("degrada a un codice stabile quando il corpo dell'errore non è JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>502</html>", { status: 502 }))
    );
    await expect(apiFetch("/api/v1/x")).rejects.toMatchObject({
      status: 502,
      code: "INTERNAL",
      retryable: false,
    });
  });

  it("B-03.2-26: una richiesta appesa oltre la soglia fallisce con TIMEOUT, non resta in sospeso", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            // Nessun timer nostro: la richiesta "resterebbe appesa"
            // indefinitamente senza l'AbortSignal composto da apiFetch.
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError"))
            );
          })
      )
    );
    const start = Date.now();
    await expect(apiFetch("/api/v1/x", {}, 20)).rejects.toMatchObject({
      code: "TIMEOUT",
      retryable: true,
    });
    // Soglia dichiarata rispettata (con margine largo per CI lento), non i
    // secondi di un eventuale timeout di default dimenticato nel test.
    expect(Date.now() - start).toBeLessThan(2000);
  });

  it("non riclassifica come TIMEOUT l'abort esplicito del chiamante (es. Stop)", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError"))
            );
          })
      )
    );
    const pending = apiFetch("/api/v1/x", { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});
