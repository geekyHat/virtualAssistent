import type { ComponentType, ReactNode } from "react";
import type { MessageKey } from "@/shared/i18n";

export interface WorkspacePanelDefinition {
  id: string;
  route: string;
  capability: string;
  labelKey: MessageKey;
  icon: ComponentType<{ className?: string }>;
  order: number;
  available: boolean;
  render: () => ReactNode;
}

/** Solo pannelli revisionati e registrati esplicitamente dalla feature. */
export function createWorkspacePanelRegistry(
  definitions: WorkspacePanelDefinition[]
): WorkspacePanelDefinition[] {
  const ids = new Set<string>();
  for (const panel of definitions) {
    if (ids.has(panel.id)) throw new Error(`Pannello duplicato: ${panel.id}`);
    ids.add(panel.id);
  }
  return [...definitions].filter((panel) => panel.available).sort((a, b) => a.order - b.order);
}
