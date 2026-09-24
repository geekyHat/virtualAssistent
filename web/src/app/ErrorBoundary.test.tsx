import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/shared/i18n";
import { ErrorBoundary } from "./ErrorBoundary";

afterEach(cleanup);

function Bomb({ armed }: { armed: boolean }): React.ReactElement {
  if (armed) throw new Error("boom");
  return <p>ok</p>;
}

describe("ErrorBoundary", () => {
  it("renderizza i children quando non c'è errore", () => {
    render(
      <I18nProvider>
        <ErrorBoundary>
          <Bomb armed={false} />
        </ErrorBoundary>
      </I18nProvider>
    );
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("mostra un fallback recuperabile quando un figlio va in crash, senza pagina bianca", () => {
    // React logga l'errore sulla console anche con un boundary che lo cattura:
    // rumore atteso, non un guasto del test.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <I18nProvider>
        <ErrorBoundary>
          <Bomb armed={true} />
        </ErrorBoundary>
      </I18nProvider>
    );
    expect(screen.getByRole("heading", { name: "Si è verificato un errore" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Riprova" })).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it("«Riprova» azzera il confine e ritenta il render degli stessi children", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    let armed = true;
    function Controlled() {
      return <Bomb armed={armed} />;
    }
    const { rerender } = render(
      <I18nProvider>
        <ErrorBoundary>
          <Controlled />
        </ErrorBoundary>
      </I18nProvider>
    );
    expect(screen.getByRole("button", { name: "Riprova" })).toBeInTheDocument();
    // Il prossimo render degli stessi children non deve più esplodere:
    // la causa reale (dati/stato) è stata risolta altrove.
    armed = false;
    await userEvent.click(screen.getByRole("button", { name: "Riprova" }));
    rerender(
      <I18nProvider>
        <ErrorBoundary>
          <Controlled />
        </ErrorBoundary>
      </I18nProvider>
    );
    expect(screen.getByText("ok")).toBeInTheDocument();
    consoleError.mockRestore();
  });
});
