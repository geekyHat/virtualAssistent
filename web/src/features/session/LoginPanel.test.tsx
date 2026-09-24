import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginPanel } from "./LoginPanel";

const baseProps = {
  pending: false,
  failed: false,
  credentialNotSet: false,
  onSubmit: vi.fn(),
  onGoToBootstrap: vi.fn(),
  bootstrapAvailable: false,
};

afterEach(cleanup);

describe("LoginPanel", () => {
  it("raccoglie nome+credenziale e avvia il login", async () => {
    const onSubmit = vi.fn();
    render(<LoginPanel {...baseProps} onSubmit={onSubmit} />);
    const submit = screen.getByRole("button", { name: "Accedi" });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox", { name: "Nome da mostrare" }), "Ada");
    await userEvent.type(screen.getByLabelText("Password locale"), "test-passphrase-1234");
    await userEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledWith({
      displayName: "Ada",
      credential: "test-passphrase-1234",
    });
  });

  it("mostra l'errore di credenziali non valide e sposta il focus", () => {
    render(<LoginPanel {...baseProps} failed />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Nome o password non validi");
    expect(alert).toHaveFocus();
  });

  it("guida al recupero esplicito quando manca la credenziale locale", () => {
    render(<LoginPanel {...baseProps} credentialNotSet />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "L'installazione esistente non ha ancora una password locale"
    );
  });

  it("propone il bootstrap quando nessuna installazione è presente", async () => {
    const onGoToBootstrap = vi.fn();
    render(<LoginPanel {...baseProps} bootstrapAvailable onGoToBootstrap={onGoToBootstrap} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Nessuna installazione presente: inizia da qui." })
    );
    expect(onGoToBootstrap).toHaveBeenCalledTimes(1);
  });

  it("B-03.2-33: due submit nello stesso tick avviano un solo login", async () => {
    const onSubmit = vi.fn();
    render(<LoginPanel {...baseProps} onSubmit={onSubmit} />);
    await userEvent.type(screen.getByRole("textbox", { name: "Nome da mostrare" }), "Ada");
    await userEvent.type(screen.getByLabelText("Password locale"), "test-passphrase-1234");
    const form = screen.getByRole("button", { name: "Accedi" }).closest("form");
    if (!form) throw new Error("form non trovato");
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});
