import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/shared/api/client";
import { ChatErrorNotice } from "./ChatErrorNotice";

afterEach(cleanup);

describe("ChatErrorNotice", () => {
  it("mostra e copia il correlation ID senza reinviare automaticamente", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(<ChatErrorNotice error={new ApiError(500, "INTERNAL", "Errore", false, "cid-123")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("cid-123");
    fireEvent.click(screen.getByRole("button", { name: "Copia" }));
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith("cid-123"));
    expect(screen.queryByRole("button", { name: "Riprova" })).toBeNull();
  });
});
