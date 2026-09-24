import { useEffect, useRef } from "react";
import { useI18n, type MessageKey } from "@/shared/i18n";
import { Alert, AlertDescription, Button, Card } from "@/shared/ui";
import type { Identity, Role } from "./types";

export interface SettingsPanelProps {
  status: "loading" | "signed-in";
  identity: Identity | null;
  revokePending: boolean;
  revokeFailed: boolean;
  onRevoke: () => void;
}

const ROLE_KEYS: Record<Role, MessageKey> = {
  owner: "role_owner",
  member: "role_member",
  administrator: "role_administrator",
};

/**
 * Pannello identità per l'area impostazioni (B-08.1).
 *
 * Presentazionale: nessun fetch diretto. Mostra identità e ruolo del
 * proprietario e la revoca della sessione con stati pending/failed e
 * alert focalizzato (comportamento ereditato da IdentityPanel).
 */
export function SettingsPanel(props: SettingsPanelProps) {
  const { t } = useI18n();
  const { status, identity, revokePending, revokeFailed, onRevoke } = props;
  const revokeErrorRef = useRef<HTMLDivElement>(null);
  // Guardia sincrona (B-03.2-33): vedi BootstrapPanel per il razionale.
  const revokingRef = useRef(false);

  useEffect(() => {
    if (revokeFailed) revokeErrorRef.current?.focus();
  }, [revokeFailed]);

  // Nessun array di dipendenze: vedi BootstrapPanel per il razionale (un
  // mutate risolto prima del prossimo commit osservabile farebbe saltare
  // la transizione a un effetto con [revokePending] come dipendenza).
  useEffect(() => {
    revokingRef.current = revokePending;
  });

  const handleRevoke = () => {
    if (revokingRef.current) return;
    revokingRef.current = true;
    onRevoke();
  };

  return (
    <Card className="mx-auto w-full max-w-md" aria-busy={status === "loading"}>
      {status === "loading" && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("loading")}
        </p>
      )}

      {status === "signed-in" && identity && (
        <>
          <h2 className="text-lg font-semibold">{t("signed_in_title")}</h2>
          <p className="text-lg font-semibold">{identity.display_name}</p>
          <p className="text-sm text-muted-foreground">{t(ROLE_KEYS[identity.role])}</p>
          <p className="text-sm text-muted-foreground">{t("signed_in_hint")}</p>
          {revokeFailed && (
            <Alert ref={revokeErrorRef} tabIndex={-1} variant="destructive">
              <AlertDescription>{t("revoke_error")}</AlertDescription>
            </Alert>
          )}
          <Button type="button" disabled={revokePending} onClick={handleRevoke}>
            {revokePending ? t("revoke_busy") : revokeFailed ? t("revoke_retry") : t("revoke")}
          </Button>
        </>
      )}
    </Card>
  );
}
