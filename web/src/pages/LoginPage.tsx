import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { LoginPanel } from "../features/session/LoginPanel";
import { useIdentity } from "../features/session/useIdentity";
import { useLogin } from "../features/session/useSession";
import { useSessionStatus } from "../features/session/useSessionStatus";
import { ApiError } from "../shared/api/client";
import { useI18n } from "@/shared/i18n";

/**
 * Pagina di rientro autenticato separata da `/` (B-03.2-14).
 *
 * - Signed-in: redirect a `/conversations` (l'area di lavoro).
 * - Signed-out + bootstrappato: form di login.
 * - Non ancora bootstrappato: redirect a `/`, che mostra il form di
 *   bootstrap. Non forziamo il flusso opposto: se l'utente arriva su
 *   `/login` senza owner, deve poter iniziare l'installazione.
 */
export function LoginPage() {
  const { t } = useI18n();
  const identity = useIdentity();
  const status = useSessionStatus();
  const login = useLogin();
  const navigate = useNavigate();

  useEffect(() => {
    if (identity.status === "signed-in") navigate("/conversations", { replace: true });
  }, [identity.status, navigate]);

  useEffect(() => {
    if (identity.status === "signed-out" && status.bootstrapped === false) {
      navigate("/", { replace: true });
    }
  }, [identity.status, status.bootstrapped, navigate]);

  if (identity.status === "loading" || status.status === "loading") {
    return (
      <p role="status" style={{ margin: 0 }}>
        {t("loading")}
      </p>
    );
  }

  const credentialNotSet =
    login.isError && login.error instanceof ApiError && login.error.code === "CREDENTIAL_NOT_SET";

  return (
    <LoginPanel
      pending={login.isPending}
      failed={login.isError && !credentialNotSet}
      credentialNotSet={credentialNotSet}
      onSubmit={(payload) => {
        if (login.isPending) return;
        login.mutate(payload);
      }}
      onGoToBootstrap={() => navigate("/", { replace: true })}
      bootstrapAvailable={status.bootstrapped === false}
    />
  );
}
