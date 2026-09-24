import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { LanguageSelect, useI18n } from "@/shared/i18n";
import { Button } from "@/shared/ui";
import { cn } from "@/shared/lib/utils";
import { useIdentity } from "@/features/session";
import { NewConversationButton } from "@/features/chat";

/**
 * Voci di navigazione (B-08.1). Conversazioni e Impostazioni sono link;
 * Documenti/Elaborati/Collegamenti sono capacità future dichiarate con lo
 * stato "in corso" — non sono pulsanti fittizi, non sono cliccabili.
 */
function NavItems() {
  const { t } = useI18n();
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    cn(
      "block rounded-md px-3 py-2 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
      isActive ? "bg-accent text-accent-foreground" : "text-foreground hover:bg-accent/60"
    );
  const disabledClass =
    "flex items-center justify-between rounded-md px-3 py-2 text-sm text-muted-foreground";

  return (
    <ul className="flex flex-col gap-1">
      <li>
        <NewConversationButton />
      </li>
      <li>
        <NavLink to="/conversations" className={linkClass}>
          {t("nav_conversations")}
        </NavLink>
      </li>
      <li>
        <span className={disabledClass} aria-disabled="true">
          {t("nav_documents")}
          <span className="text-xs">{t("nav_in_progress")}</span>
        </span>
      </li>
      <li>
        <span className={disabledClass} aria-disabled="true">
          {t("nav_artifacts")}
          <span className="text-xs">{t("nav_in_progress")}</span>
        </span>
      </li>
      <li>
        <span className={disabledClass} aria-disabled="true">
          {t("nav_links")}
          <span className="text-xs">{t("nav_in_progress")}</span>
        </span>
      </li>
      <li>
        <NavLink to="/settings" className={linkClass}>
          {t("nav_settings")}
        </NavLink>
      </li>
    </ul>
  );
}

/**
 * Shell desktop/mobile (B-08.1).
 *
 * Desktop: sidebar (brand, nav, identità + lingua) + area centrale.
 * Mobile: top bar con menu accessibile (aria-expanded/aria-controls).
 *
 * Focus post-navigazione: al cambio di pathname il focus va sull'h1
 * dell'area principale (tabIndex={-1}, senza scroll forzato). Il primo
 * render non ruba il focus.
 */
export function Shell({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const identity = useIdentity();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const firstRender = useRef(true);

  // La navigazione chiude il menu mobile.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  // Focus sull'h1 dell'area principale dopo ogni navigazione (non al mount).
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    const heading = mainRef.current?.querySelector("h1");
    (heading as HTMLHeadingElement | null)?.focus({ preventScroll: true });
  }, [location.pathname]);

  const displayName = identity.identity?.display_name;

  return (
    <div className="flex min-h-dvh flex-col md:flex-row">
      <aside className="hidden w-64 shrink-0 flex-col gap-6 border-r border-border p-6 md:flex">
        <span className="text-lg font-semibold">NewRay</span>
        <nav aria-label={t("nav_label")}>
          <NavItems />
        </nav>
        <div className="mt-auto flex flex-col gap-4">
          {displayName && (
            <p className="truncate text-sm font-medium" title={displayName}>
              {displayName}
            </p>
          )}
          <LanguageSelect id="newray-language-sidebar" />
        </div>
      </aside>

      <header className="flex items-center justify-between border-b border-border p-4 md:hidden">
        <span className="text-lg font-semibold">NewRay</span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-expanded={menuOpen}
          aria-controls="newray-mobile-nav"
          onClick={() => setMenuOpen((value) => !value)}
        >
          {t("nav_menu")}
        </Button>
      </header>

      {menuOpen && (
        <nav
          id="newray-mobile-nav"
          aria-label={t("nav_label")}
          className="flex flex-col gap-4 border-b border-border p-4 md:hidden"
        >
          <NavItems />
          {displayName && (
            <p className="truncate text-sm font-medium" title={displayName}>
              {displayName}
            </p>
          )}
          <LanguageSelect id="newray-language-mobile" />
        </nav>
      )}

      <main ref={mainRef} className="flex-1 p-6">
        {children}
      </main>
    </div>
  );
}
