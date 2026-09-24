import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/shared/i18n";
import { Alert, AlertDescription, Button, Card, Input, Label } from "@/shared/ui";

export interface LoginPanelProps {
  pending: boolean;
  failed: boolean;
  credentialNotSet: boolean;
  onSubmit: (payload: { displayName: string; credential: string }) => void;
  onGoToBootstrap: () => void;
  bootstrapAvailable: boolean;
}

/**
 * Pannello di rientro autenticato (B-03.2-14).
 *
 * Presentazionale: nessun fetch diretto. Il caso `credentialNotSet` non
 * è un errore di digitazione — indica un owner senza credenziale locale
 * e propone il recupero esplicito (comando da riga di comando), non un
 * secondo bootstrap che sovrascriverebbe l'installazione.
 */
export function LoginPanel(props: LoginPanelProps) {
  const { t } = useI18n();
  const { pending, failed, credentialNotSet, onSubmit, onGoToBootstrap, bootstrapAvailable } =
    props;
  const [name, setName] = useState("");
  const [credential, setCredential] = useState("");
  const errorRef = useRef<HTMLDivElement>(null);
  // Guardia sincrona (B-03.2-33): vedi BootstrapPanel per il razionale —
  // `pending` è stato React, riflesso nel DOM solo dopo un render.
  const submittingRef = useRef(false);

  useEffect(() => {
    if (failed || credentialNotSet) errorRef.current?.focus();
  }, [failed, credentialNotSet]);

  // Nessun array di dipendenze: vedi BootstrapPanel per il razionale (un
  // mutate risolto prima del prossimo commit osservabile farebbe saltare
  // la transizione a un effetto con [pending] come dipendenza).
  useEffect(() => {
    submittingRef.current = pending;
  });

  const submitDisabled = pending || name.trim().length === 0 || credential.trim().length < 8;

  return (
    <Card className="mx-auto w-full max-w-md">
      <h1 className="text-xl font-semibold">{t("login_title")}</h1>
      <p className="text-sm text-muted-foreground">{t("login_hint")}</p>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (submittingRef.current) return;
          const trimmedName = name.trim();
          const trimmedCredential = credential.trim();
          if (trimmedName && trimmedCredential.length >= 8) {
            submittingRef.current = true;
            onSubmit({ displayName: trimmedName, credential: trimmedCredential });
          }
        }}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="newray-login-name">{t("name_label")}</Label>
          <Input
            id="newray-login-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t("name_placeholder")}
            autoComplete="username"
            maxLength={100}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="newray-login-credential">{t("credential_label")}</Label>
          <Input
            id="newray-login-credential"
            type="password"
            value={credential}
            onChange={(event) => setCredential(event.target.value)}
            placeholder={t("credential_placeholder")}
            autoComplete="current-password"
            minLength={8}
            maxLength={4096}
          />
        </div>
        <Button type="submit" disabled={submitDisabled}>
          {pending ? t("login_busy") : failed || credentialNotSet ? t("login_retry") : t("login")}
        </Button>
        {credentialNotSet && (
          <Alert ref={errorRef} tabIndex={-1} variant="destructive">
            <AlertDescription>{t("login_credential_not_set")}</AlertDescription>
          </Alert>
        )}
        {failed && !credentialNotSet && (
          <Alert ref={errorRef} tabIndex={-1} variant="destructive">
            <AlertDescription>{t("login_error")}</AlertDescription>
          </Alert>
        )}
      </form>
      {bootstrapAvailable && (
        <Button type="button" variant="link" className="w-fit px-0" onClick={onGoToBootstrap}>
          {t("login_go_to_bootstrap")}
        </Button>
      )}
    </Card>
  );
}
