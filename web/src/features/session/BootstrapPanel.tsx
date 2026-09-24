import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/shared/i18n";
import { Alert, AlertDescription, Button, Card, Input, Label } from "@/shared/ui";

export interface BootstrapPanelProps {
  status: "loading" | "signed-out" | "error";
  message: string | null;
  /** Bootstrap rifiutato (409): l'installazione esiste già. */
  conflict: boolean;
  /** Se true, il pannello signed-out mostra un collegamento al login. */
  bootstrapped: boolean;
  bootstrapPending: boolean;
  bootstrapFailed: boolean;
  onBootstrap: (payload: { displayName: string; credential: string }) => void;
  onRetry: () => void;
  onDetect: () => void;
  onGoToLogin: () => void;
}

/**
 * Pannello di primo avvio (bootstrap) e degli stati non autenticati.
 *
 * Presentazionale: nessun fetch diretto. Stati (NewRay.md §18.3):
 * caricamento, non autenticato con bootstrap, conflitto recuperabile,
 * errore con retry.
 */
export function BootstrapPanel(props: BootstrapPanelProps) {
  const { t } = useI18n();
  const {
    status,
    message,
    conflict,
    bootstrapped,
    bootstrapPending,
    bootstrapFailed,
    onBootstrap,
    onRetry,
    onDetect,
    onGoToLogin,
  } = props;
  const [name, setName] = useState("");
  const [credential, setCredential] = useState("");
  const bootstrapErrorRef = useRef<HTMLDivElement>(null);
  // Guardia sincrona (B-03.2-33): `bootstrapPending` è stato React, riflesso
  // nel DOM solo dopo un render. Due submit nello stesso tick (Enter+click,
  // doppio click reale) leggono entrambi `isPending` non ancora aggiornato.
  // Il ref è sincrono: il secondo submit lo trova già vero.
  const submittingRef = useRef(false);

  useEffect(() => {
    if (bootstrapFailed) bootstrapErrorRef.current?.focus();
  }, [bootstrapFailed]);

  // Nessun array di dipendenze: un mutate che risolve prima del prossimo
  // commit osservabile farebbe saltare la transizione true→false a un
  // effetto con [bootstrapPending] come dipendenza (React può comprimere
  // pending+settled in un solo render). Sincronizzarsi a ogni render
  // resta corretto qualunque sia la granularità dei commit intermedi.
  useEffect(() => {
    submittingRef.current = bootstrapPending;
  });

  return (
    <Card className="mx-auto w-full max-w-md" aria-busy={status === "loading"}>
      {status === "loading" && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("loading")}
        </p>
      )}

      {status === "signed-out" && conflict && (
        <>
          <h1 className="text-xl font-semibold">{t("conflict_title")}</h1>
          <p className="text-sm text-muted-foreground">{t("conflict_hint")}</p>
          <div className="flex flex-col items-start gap-2">
            <Button type="button" onClick={onGoToLogin}>
              {t("login")}
            </Button>
            <Button type="button" variant="link" className="w-fit px-0" onClick={onDetect}>
              {t("detect")}
            </Button>
          </div>
        </>
      )}

      {status === "signed-out" && !conflict && (
        <>
          <h1 className="text-xl font-semibold">{t("signed_out_title")}</h1>
          <p className="text-sm text-muted-foreground">{t("signed_out_hint")}</p>
          <form
            className="flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (submittingRef.current) return;
              const trimmedName = name.trim();
              const trimmedCredential = credential.trim();
              if (trimmedName && trimmedCredential.length >= 8) {
                submittingRef.current = true;
                onBootstrap({ displayName: trimmedName, credential: trimmedCredential });
              }
            }}
          >
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="newray-display-name">{t("name_label")}</Label>
              <Input
                id="newray-display-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("name_placeholder")}
                autoComplete="name"
                maxLength={100}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="newray-credential">{t("credential_label")}</Label>
              <Input
                id="newray-credential"
                type="password"
                value={credential}
                onChange={(event) => setCredential(event.target.value)}
                placeholder={t("credential_placeholder")}
                autoComplete="new-password"
                minLength={8}
                maxLength={4096}
              />
              <p className="text-xs text-muted-foreground">{t("credential_hint")}</p>
            </div>
            <Button
              type="submit"
              disabled={
                bootstrapPending || name.trim().length === 0 || credential.trim().length < 8
              }
            >
              {bootstrapPending
                ? t("bootstrap_busy")
                : bootstrapFailed
                  ? t("bootstrap_retry")
                  : t("bootstrap")}
            </Button>
          </form>
          {bootstrapFailed && (
            <Alert ref={bootstrapErrorRef} tabIndex={-1} variant="destructive">
              <AlertDescription>{t("bootstrap_error")}</AlertDescription>
            </Alert>
          )}
          {bootstrapped && (
            <Button type="button" variant="link" className="w-fit px-0" onClick={onGoToLogin}>
              {t("bootstrap_go_to_login")}
            </Button>
          )}
        </>
      )}

      {status === "error" && (
        <>
          <h1 className="text-xl font-semibold">{t("error_title")}</h1>
          <Alert variant="destructive">
            <AlertDescription>{message ?? t("error_title")}</AlertDescription>
          </Alert>
          <Button type="button" onClick={onRetry}>
            {t("retry")}
          </Button>
        </>
      )}
    </Card>
  );
}
