import { useEffect, useId, useRef, useState } from "react";
import { PanelRightClose, PanelRightOpen, X } from "lucide-react";
import { useLocation } from "react-router-dom";
import { useI18n } from "@/shared/i18n";
import { Button } from "@/shared/ui";
import type { WorkspacePanelDefinition } from "@/shared/lib/workspacePanels";

export function WorkspacePanel({ panels }: { panels: WorkspacePanelDefinition[] }) {
  const { t } = useI18n();
  const location = useLocation();
  const [activeId, setActiveId] = useState(panels[0]?.id);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const active = panels.find((panel) => panel.id === activeId) ?? panels[0];

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const dialog = dialogRef.current;
    dialog?.querySelector<HTMLButtonElement>("[data-workspace-close]")?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileOpen(false);
        triggerRef.current?.focus();
        return;
      }
      if (event.key !== "Tab" || !dialog) return;
      const focusable = [
        ...dialog.querySelectorAll<HTMLElement>("button, input, select, a[href]"),
      ].filter(
        (element) => !element.hasAttribute("disabled") && element.getClientRects().length > 0
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [mobileOpen]);

  const content = (compact: boolean, id: string) => (
    <>
      <div className="flex items-center justify-between border-b border-border p-2">
        {!compact && <span className="px-2 text-sm font-semibold">{t("workspace_options")}</span>}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="hidden lg:inline-flex"
          aria-label={compact ? t("workspace_expand") : t("workspace_collapse")}
          onClick={() => setCollapsed((value) => !value)}
        >
          {compact ? <PanelRightOpen size={18} /> : <PanelRightClose size={18} />}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="lg:hidden"
          data-workspace-close
          aria-label={t("workspace_close")}
          onClick={() => {
            setMobileOpen(false);
            triggerRef.current?.focus();
          }}
        >
          <X size={18} />
        </Button>
      </div>
      <div className="flex min-h-0 flex-1">
        <nav
          className="flex shrink-0 flex-col gap-1 border-r border-border p-2"
          aria-label={t("workspace_options")}
        >
          {panels.map((panel) => {
            const Icon = panel.icon;
            return (
              <Button
                key={panel.id}
                type="button"
                variant={panel.id === active?.id ? "secondary" : "ghost"}
                size="sm"
                className="h-10 w-10 p-0"
                aria-label={t(panel.labelKey)}
                aria-controls={!compact ? id : undefined}
                aria-expanded={panel.id === active?.id && !compact}
                onClick={() => {
                  setActiveId(panel.id);
                  setCollapsed(false);
                }}
              >
                <Icon className="size-5" />
              </Button>
            );
          })}
        </nav>
        {!compact && active && (
          <section
            id={id}
            className="min-w-0 flex-1 overflow-y-auto p-3"
            aria-label={t(active.labelKey)}
          >
            <h2 className="mb-3 text-sm font-semibold">{t(active.labelKey)}</h2>
            {active.render()}
          </section>
        )}
      </div>
    </>
  );

  return (
    <>
      <aside
        className={`hidden shrink-0 flex-col overflow-hidden rounded-lg border border-border bg-background lg:flex ${collapsed ? "w-16" : "w-80"}`}
        aria-label={t("workspace_options")}
      >
        {content(collapsed, `${panelId}-desktop`)}
      </aside>
      <Button
        ref={triggerRef}
        type="button"
        variant="outline"
        size="sm"
        className="fixed right-4 top-20 z-20 lg:hidden"
        aria-label={t("workspace_open")}
        aria-expanded={mobileOpen}
        onClick={() => setMobileOpen(true)}
      >
        <PanelRightOpen size={18} />
      </Button>
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onMouseDown={() => {
            setMobileOpen(false);
            triggerRef.current?.focus();
          }}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label={t("workspace_options")}
            className="ml-auto flex h-full w-[min(24rem,calc(100vw-2rem))] flex-col bg-background shadow-xl"
            onMouseDown={(event) => event.stopPropagation()}
          >
            {content(false, `${panelId}-mobile`)}
          </div>
        </div>
      )}
    </>
  );
}
