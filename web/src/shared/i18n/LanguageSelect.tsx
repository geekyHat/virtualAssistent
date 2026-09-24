import { Label } from "@/shared/ui";
import { cn } from "@/shared/lib/utils";
import { LOCALES, useI18n, type Locale } from "./index";

/**
 * Selettore lingua IT/EN (B-08.1): nativo, accessibile e senza dipendenze
 * aggiuntive. La preferenza è persistita dal provider i18n.
 */
export function LanguageSelect({ id, className }: { id: string; className?: string }) {
  const { locale, setLocale, t } = useI18n();
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id}>{t("settings_language")}</Label>
      <select
        id={id}
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
        className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        {LOCALES.map((item) => (
          <option key={item} value={item}>
            {item.toUpperCase()}
          </option>
        ))}
      </select>
    </div>
  );
}
