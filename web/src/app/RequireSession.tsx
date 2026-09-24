import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useIdentity } from "@/features/session";
import { useI18n } from "@/shared/i18n";
import { Alert, AlertDescription, Button, Skeleton } from "@/shared/ui";

/**
 * Guarda di sessione lato UX (B-08.1). Il server autorizza sempre: qui
 * gestiamo solo l'esperienza.
 *
 * - loading → skeleton
 * - signed-out → redirect a /login
 * - signed-in → children
 * - error → stato recuperabile con retry
 */
export function RequireSession({ children }: { children: ReactNode }) {
  const identity = useIdentity();
  const { t } = useI18n();

  if (identity.status === "loading") {
    return (
      <div className="flex justify-center p-8" role="status" aria-label={t("loading")}>
        <Skeleton className="h-40 w-full max-w-md" />
      </div>
    );
  }

  if (identity.status === "signed-out") {
    return <Navigate to="/login" replace />;
  }

  if (identity.status === "error") {
    return (
      <div className="mx-auto flex w-full max-w-md flex-col gap-4 p-8">
        <Alert variant="destructive">
          <AlertDescription>{identity.message ?? t("error_title")}</AlertDescription>
        </Alert>
        <Button type="button" onClick={identity.refetch}>
          {t("retry")}
        </Button>
      </div>
    );
  }

  return <>{children}</>;
}
