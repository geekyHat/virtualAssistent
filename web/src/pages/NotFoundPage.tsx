import { Link } from "react-router-dom";
import { Button } from "@/shared/ui";
import { useI18n } from "@/shared/i18n";

/**
 * 404 recuperabile (B-08.1): il percorso non esiste, ma l'utente non è
 * bloccato — un link riporta alla home.
 */
export function NotFoundPage() {
  const { t } = useI18n();
  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-4">
      <h1 tabIndex={-1} className="text-2xl font-semibold outline-none">
        {t("notfound_title")}
      </h1>
      <p className="text-sm text-muted-foreground">{t("notfound_hint")}</p>
      <Button asChild variant="link" className="w-fit px-0">
        <Link to="/">{t("notfound_link")}</Link>
      </Button>
    </div>
  );
}
