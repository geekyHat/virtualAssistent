import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BootstrapPanel, useBootstrap, useIdentity } from "../features/session";
import { useSessionStatus } from "../features/session/useSessionStatus";
import { ApiError } from "../shared/api/client";

/**
 * Landing dell'installazione (A-04, B-03.2-14, B-08.1).
 *
 * - Signed-in: redirect a `/conversations` (l'area di lavoro).
 * - Signed-out + non bootstrappato: form di bootstrap (con credenziale).
 * - Signed-out + bootstrappato: redirect a `/login`. L'utente non deve
 *   ricreare l'owner: la ricreazione silenziosa è vietata (B-03.2-14).
 */
export function IdentityPage() {
  const identity = useIdentity();
  const status = useSessionStatus();
  const bootstrap = useBootstrap();
  const [conflict, setConflict] = useState(false);
  const navigate = useNavigate();

  const bootstrapped = status.bootstrapped === true;

  useEffect(() => {
    // I redirect avvengono solo dopo che identity e status hanno risolto:
    // così non si sostituisce il pannello di bootstrap con /login mentre
    // la GET /session/status è ancora in flight (falso positivo).
    if (identity.status === "signed-in") {
      navigate("/conversations", { replace: true });
    } else if (identity.status === "signed-out" && bootstrapped && !conflict) {
      navigate("/login", { replace: true });
    }
  }, [identity.status, bootstrapped, conflict, navigate]);

  const handleBootstrap = (payload: { displayName: string; credential: string }) => {
    if (bootstrap.isPending) return;
    setConflict(false);
    bootstrap.mutate(payload, {
      onSuccess: () => navigate("/conversations", { replace: true }),
      onError: (error) => {
        if (error instanceof ApiError && error.code === "CONFLICT") setConflict(true);
      },
    });
  };

  const panelStatus =
    identity.status === "signed-in"
      ? "loading"
      : identity.status === "error"
        ? "error"
        : "signed-out";

  return (
    <BootstrapPanel
      status={panelStatus}
      message={identity.message}
      conflict={conflict}
      bootstrapped={bootstrapped}
      bootstrapPending={bootstrap.isPending}
      bootstrapFailed={bootstrap.isError && !conflict}
      onBootstrap={handleBootstrap}
      onRetry={identity.refetch}
      onDetect={() => {
        setConflict(false);
        identity.refetch();
      }}
      onGoToLogin={() => navigate("/login")}
    />
  );
}
