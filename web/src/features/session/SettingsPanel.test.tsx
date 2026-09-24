import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SettingsPanel } from "./SettingsPanel";

const baseProps = {
  identity: null,
  revokePending: false,
  revokeFailed: false,
  onRevoke: vi.fn(),
};

afterEach(cleanup);

describe("SettingsPanel", () => {
  it("mostra lo stato di caricamento", () => {
    render(<SettingsPanel {...baseProps} status="loading" />);
    expect(screen.getByRole("status")).toHaveTextContent("Caricamento");
  });

  it("mostra identità e termine della sessione quando autenticato", async () => {
    const onRevoke = vi.fn();
    render(
      <SettingsPanel
        {...baseProps}
        status="signed-in"
        identity={{ user_id: "u1", display_name: "Ada", role: "owner" }}
        onRevoke={onRevoke}
      />
    );
    expect(screen.getByText("Ada")).toBeInTheDocument();
    expect(screen.getByText("Proprietario")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Termina la sessione" }));
    expect(onRevoke).toHaveBeenCalledTimes(1);
  });

  it("mostra l'errore di revoca, sposta il focus e consente un retry", async () => {
    const onRevoke = vi.fn();
    render(
      <SettingsPanel
        {...baseProps}
        status="signed-in"
        identity={{ user_id: "u1", display_name: "Ada", role: "owner" }}
        revokeFailed
        onRevoke={onRevoke}
      />
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Non è stato possibile terminare la sessione");
    expect(alert).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: "Riprova a terminare la sessione" }));
    expect(onRevoke).toHaveBeenCalledTimes(1);
  });

  it("disabilita la sola azione di revoca quando è in corso", () => {
    render(
      <SettingsPanel
        {...baseProps}
        status="signed-in"
        identity={{ user_id: "u1", display_name: "Ada", role: "owner" }}
        revokePending
      />
    );
    expect(screen.getByRole("button", { name: "Chiusura della sessione…" })).toBeDisabled();
  });

  it("B-03.2-33: due click nello stesso tick avviano una sola revoca", async () => {
    const onRevoke = vi.fn();
    render(
      <SettingsPanel
        {...baseProps}
        status="signed-in"
        identity={{ user_id: "u1", display_name: "Ada", role: "owner" }}
        onRevoke={onRevoke}
      />
    );
    const button = screen.getByRole("button", { name: "Termina la sessione" });
    // Due click sincroni, senza `await` fra loro: solo la guardia
    // sincrona (ref) può impedire il secondo invio prima del re-render.
    fireEvent.click(button);
    fireEvent.click(button);
    expect(onRevoke).toHaveBeenCalledTimes(1);
  });
});
