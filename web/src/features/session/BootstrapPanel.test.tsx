import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BootstrapPanel } from "./BootstrapPanel";

const baseProps = {
  message: null,
  conflict: false,
  bootstrapped: false,
  bootstrapPending: false,
  bootstrapFailed: false,
  onBootstrap: vi.fn(),
  onRetry: vi.fn(),
  onDetect: vi.fn(),
  onGoToLogin: vi.fn(),
};

afterEach(cleanup);

describe("BootstrapPanel", () => {
  it("mostra lo stato di caricamento", () => {
    render(<BootstrapPanel {...baseProps} status="loading" />);
    expect(screen.getByRole("status")).toHaveTextContent("Caricamento");
  });

  it("raccoglie nome+credenziale e avvia il bootstrap", async () => {
    const onBootstrap = vi.fn();
    render(<BootstrapPanel {...baseProps} status="signed-out" onBootstrap={onBootstrap} />);
    const submit = screen.getByRole("button", { name: "Inizia NewRay" });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Nome da mostrare" }), "Ada");
    // La credenziale è troppo corta: il bottone resta disabilitato.
    await userEvent.type(screen.getByLabelText("Password locale"), "corta");
    expect(submit).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Password locale"));
    await userEvent.type(screen.getByLabelText("Password locale"), "test-passphrase-1234");
    await userEvent.click(submit);
    expect(onBootstrap).toHaveBeenCalledWith({
      displayName: "Ada",
      credential: "test-passphrase-1234",
    });
  });

  it(
    "B-03.2-33: due submit nello stesso tick (prima che React re-renderizzi " +
      "il pulsante disabilitato) avviano un solo bootstrap",
    async () => {
      const onBootstrap = vi.fn();
      render(<BootstrapPanel {...baseProps} status="signed-out" onBootstrap={onBootstrap} />);
      await userEvent.type(screen.getByRole("textbox", { name: "Nome da mostrare" }), "Ada");
      await userEvent.type(screen.getByLabelText("Password locale"), "test-passphrase-1234");
      const form = screen.getByRole("button", { name: "Inizia NewRay" }).closest("form");
      if (!form) throw new Error("form non trovato");
      // Due dispatch sincroni, senza `await` fra loro: react-query non ha
      // ancora avuto occasione di ri-renderizzare `disabled`, quindi solo
      // la guardia sincrona (ref) può impedire il secondo invio.
      fireEvent.submit(form);
      fireEvent.submit(form);
      expect(onBootstrap).toHaveBeenCalledTimes(1);
    }
  );

  it("propone il link al login quando l'installazione è già presente", async () => {
    const onGoToLogin = vi.fn();
    render(
      <BootstrapPanel {...baseProps} status="signed-out" bootstrapped onGoToLogin={onGoToLogin} />
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Hai già un'installazione? Accedi." })
    );
    expect(onGoToLogin).toHaveBeenCalledTimes(1);
  });

  it("mostra il conflitto 409 con accesso e rilevamento", async () => {
    const onDetect = vi.fn();
    const onGoToLogin = vi.fn();
    render(
      <BootstrapPanel
        {...baseProps}
        status="signed-out"
        conflict
        onDetect={onDetect}
        onGoToLogin={onGoToLogin}
      />
    );
    expect(screen.getByText("NewRay è già stato inizializzato")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Accedi" }));
    expect(onGoToLogin).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Rileva la sessione" }));
    expect(onDetect).toHaveBeenCalledTimes(1);
  });

  it("mostra l'errore di bootstrap, sposta il focus, conserva il nome e permette il retry", async () => {
    render(<BootstrapPanel {...baseProps} status="signed-out" bootstrapFailed />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Non è stato possibile inizializzare NewRay");
    expect(alert).toHaveFocus();
    const input = screen.getByRole("textbox", { name: "Nome da mostrare" });
    await userEvent.type(input, "Ada");
    await userEvent.type(screen.getByLabelText("Password locale"), "test-passphrase-1234");
    expect(screen.getByRole("button", { name: "Riprova l'inizializzazione" })).toBeEnabled();
    expect(input).toHaveValue("Ada");
  });

  it("mostra l'errore con azione di recupero", async () => {
    const onRetry = vi.fn();
    render(
      <BootstrapPanel
        {...baseProps}
        status="error"
        message="errore interno del server"
        onRetry={onRetry}
      />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("errore interno del server");
    await userEvent.click(screen.getByRole("button", { name: "Riprova" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
