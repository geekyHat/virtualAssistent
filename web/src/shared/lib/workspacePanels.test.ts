import { describe, expect, it } from "vitest";
import { createWorkspacePanelRegistry, type WorkspacePanelDefinition } from "./workspacePanels";

const icon = () => null;
const panel = (id: string, order: number, available = true): WorkspacePanelDefinition => ({
  id,
  route: `/${id}`,
  capability: `${id}.read`,
  order,
  available,
  icon,
  labelKey: "workspace_options",
  render: () => null,
});

describe("workspace panel registry", () => {
  it("ordina e filtra solo le sezioni disponibili", () => {
    expect(
      createWorkspacePanelRegistry([
        panel("models", 30),
        panel("future", 20, false),
        panel("chat", 10),
      ]).map((item) => item.id)
    ).toEqual(["chat", "models"]);
  });

  it("rifiuta ID duplicati anche quando una sezione è indisponibile", () => {
    expect(() =>
      createWorkspacePanelRegistry([panel("chat", 10), panel("chat", 20, false)])
    ).toThrow("Pannello duplicato: chat");
  });
});
