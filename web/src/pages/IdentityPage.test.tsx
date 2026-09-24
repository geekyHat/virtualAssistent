import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IdentityPage } from "./IdentityPage";

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

const unauthenticated = {
  code: "UNAUTHENTICATED",
  message: "mancante cookie di sessione",
  retryable: false,
  correlation_id: "c1",
};

function renderPage(handler: (url: string, method: string) => { status: number; body: unknown }) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    // La query /session/status è ortogonale al ramo autenticato di questi
    // test (che riguardano il pannello): rispondiamo "non bootstrappato"
    // in modo predefinito così IdentityPage non innesca il redirect a
    // /login. Un test dedicato può ancora forzare il caso "bootstrappato"
    // ma non lo facciamo qui: quello scenario è di LoginPage.
    if (method === "GET" && url === "/api/v1/session/status") {
      return jsonResponse(200, { bootstrapped: false });
    }
    const route = handler(url, method);
    return jsonResponse(route.status, route.body);
  });
  vi.stubGlobal("fetch", fetchMock);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<IdentityPage />} />
          <Route path="/login" element={<div>login</div>} />
          <Route path="/conversations" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { fetchMock };
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("IdentityPage", () => {
  it("reindirizza a /conversations quando la sessione è attiva", async () => {
    renderPage((url, method) => {
      expect(method).toBe("GET");
      expect(url).toBe("/api/v1/me");
      return {
        status: 200,
        body: { user_id: "u1", display_name: "Ada", role: "owner" },
      };
    });
    expect(await screen.findByTestId("location")).toHaveTextContent("/conversations");
  });

  it("parte dal bootstrap quando non c'è sessione (401)", async () => {
    renderPage(() => ({ status: 401, body: unauthenticated }));
    expect(await screen.findByRole("button", { name: "Inizia NewRay" })).toBeInTheDocument();
  });

  it("parte dal bootstrap anche con cookie già revocato (401 SESSION_INVALID)", async () => {
    // Percorso post-rivoca: il cookie scaduto/revocato resta nel browser e
    // il server risponde 401 SESSION_INVALID: lo stato è comunque "non autenticato".
    renderPage(() => ({
      status: 401,
      body: {
        code: "SESSION_INVALID",
        message: "sessione scaduta o revocata",
        retryable: false,
        correlation_id: "c4",
      },
    }));
    expect(await screen.findByRole("button", { name: "Inizia NewRay" })).toBeInTheDocument();
  });

  it("inicializza con il bootstrap e reindirizza a /conversations", async () => {
    const identity = { user_id: "u1", display_name: "Ada", role: "owner" };
    let bootstrapped = false;
    renderPage((url, method) => {
      if (method === "POST" && url === "/api/v1/session") {
        bootstrapped = true;
        return { status: 201, body: identity };
      }
      // Finché il bootstrap non è avvenuto, /me risponde 401: senza questa
      // distinzione la pagina reindirizzerebbe a /conversations prima che
      // l'utente compili il form.
      if (method === "GET" && url === "/api/v1/me" && bootstrapped) {
        return { status: 200, body: identity };
      }
      return { status: 401, body: unauthenticated };
    });
    const submit = await screen.findByRole("button", { name: "Inizia NewRay" });
    await userEvent.type(screen.getByRole("textbox", { name: "Nome da mostrare" }), "Ada");
    await userEvent.type(screen.getByLabelText("Password locale"), "test-passphrase-1234");
    await userEvent.click(submit);
    expect(await screen.findByTestId("location")).toHaveTextContent("/conversations");
  });

  it("mostra il conflitto recuperabile quando il bootstrap è già avvenuto", async () => {
    renderPage((url, method) => {
      if (method === "POST" && url === "/api/v1/session") {
        return {
          status: 409,
          body: {
            code: "CONFLICT",
            message: "il proprietario è già stato creato",
            retryable: false,
            correlation_id: "c2",
          },
        };
      }
      return { status: 401, body: unauthenticated };
    });
    const submit = await screen.findByRole("button", { name: "Inizia NewRay" });
    await userEvent.type(screen.getByRole("textbox", { name: "Nome da mostrare" }), "Ada");
    await userEvent.type(screen.getByLabelText("Password locale"), "test-passphrase-1234");
    await userEvent.click(submit);
    expect(await screen.findByText("NewRay è già stato inizializzato")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rileva la sessione" })).toBeInTheDocument();
  });

  it("mostra e riprova l'errore di bootstrap senza scambiarlo per un errore di lettura", async () => {
    let bootstrapCalls = 0;
    renderPage((url, method) => {
      if (method === "POST" && url === "/api/v1/session") {
        bootstrapCalls += 1;
        return {
          status: 500,
          body: {
            code: "INTERNAL",
            message: "errore interno del server",
            retryable: true,
            correlation_id: "c5",
          },
        };
      }
      return { status: 401, body: unauthenticated };
    });
    await userEvent.type(await screen.findByRole("textbox", { name: "Nome da mostrare" }), "Ada");
    await userEvent.type(screen.getByLabelText("Password locale"), "test-passphrase-1234");
    await userEvent.click(screen.getByRole("button", { name: "Inizia NewRay" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Non è stato possibile inizializzare NewRay");
    expect(alert).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: "Riprova l'inizializzazione" }));
    await vi.waitFor(() => expect(bootstrapCalls).toBe(2));
  });

  it("mostra l'errore con retry su fallimento del server", async () => {
    const { fetchMock } = renderPage(() => ({
      status: 500,
      body: {
        code: "INTERNAL",
        message: "errore interno del server",
        retryable: true,
        correlation_id: "c3",
      },
    }));
    expect(await screen.findByRole("alert")).toHaveTextContent("errore interno del server");
    const meCalls = () =>
      fetchMock.mock.calls.filter(([input]) => String(input) === "/api/v1/me").length;
    const before = meCalls();
    await userEvent.click(screen.getByRole("button", { name: "Riprova" }));
    // Il retry rilancia esattamente la richiesta /me; il fallimento resta visibile.
    await vi.waitFor(() => expect(meCalls()).toBe(before + 1));
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
