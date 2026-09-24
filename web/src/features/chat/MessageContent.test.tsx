import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessageContent } from "./MessageContent";

afterEach(cleanup);

describe("MessageContent", () => {
  it("renderizza Markdown e neutralizza HTML, URL attivi e immagini remote", () => {
    const { container } = render(
      <MessageContent
        content={
          '**Ciao** <img src="https://example.test/tracker" onerror="alert(1)"> <span style="background:url(https://example.test/track)">x</span> [male](javascript:alert(1)) <script>alert(1)</script>'
        }
      />
    );
    expect(screen.getByText("Ciao").tagName).toBe("STRONG");
    expect(container.querySelector("img,script,iframe")).toBeNull();
    expect(container.querySelector("[style],[src]")).toBeNull();
    expect(container.querySelector("a")?.getAttribute("href")).toBeNull();
  });

  it("copia il testo del blocco codice, non il markup", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(<MessageContent content={"```html\n<script>alert(1)</script>\n```"} />);
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copia" }));
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith("<script>alert(1)</script>"));
  });
});
