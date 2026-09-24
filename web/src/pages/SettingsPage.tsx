import { useNavigate } from "react-router-dom";
import { SettingsPanel, useIdentity, useRevoke } from "@/features/session";
import { LanguageSelect, useI18n } from "@/shared/i18n";
import { AssistantSettings } from "@/features/chat";

/**
 * Area impostazioni (B-08.1): identità + termine sessione e selettore
 * lingua IT/EN. La revoca svuota la cache client (useRevoke) e la pagina
 * è protetta da RequireSession: dopo il 204 il refetch /me risponde 401
 * e la guard porta a /login.
 */
export function SettingsPage() {
  const { t } = useI18n();
  const identity = useIdentity();
  const revoke = useRevoke();
  const navigate = useNavigate();

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <h1 tabIndex={-1} className="text-2xl font-semibold outline-none">
        {t("settings_title")}
      </h1>
      <SettingsPanel
        status={identity.status === "signed-in" ? "signed-in" : "loading"}
        identity={identity.identity}
        revokePending={revoke.isPending}
        revokeFailed={revoke.isError}
        onRevoke={() => {
          if (revoke.isPending) return;
          revoke.mutate(undefined, {
            // Fallback esplicito: se il refetch /me non risolve in tempo,
            // la guard non deve lasciarci appesi su questa pagina.
            onSuccess: () => navigate("/login", { replace: true }),
          });
        }}
      />
      <LanguageSelect id="newray-language-settings" />
      <details className="rounded-lg border border-border p-4">
        <summary className="cursor-pointer font-medium">{t("settings_advanced_models")}</summary>
        <p className="my-3 text-sm text-muted-foreground">{t("settings_advanced_hint")}</p>
        <AssistantSettings />
      </details>
    </div>
  );
}
