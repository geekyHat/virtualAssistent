/**
 * Provider i18n: il locale è stato dell'app, persistito in
 * `localStorage["newray.locale"]` (unica preferenza non sensibile; i token
 * restano in cookie HttpOnly). `document.documentElement.lang` segue il
 * locale attivo.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { MESSAGES, type Locale, type MessageKey } from "./index";

export interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

// Valore di fallback (locale it, senza persistenza): i componenti possono
// essere montati in test senza provider; l'app reale è sempre dentro I18nProvider.
const FALLBACK: I18nContextValue = {
  locale: "it",
  setLocale: () => {},
  t: (key) => MESSAGES.it[key],
};

const STORAGE_KEY = "newray.locale";

function readStoredLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "en" ? "en" : "it";
  } catch {
    return "it";
  }
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readStoredLocale);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage non disponibile: la preferenza non persiste, ma resta attiva.
    }
  }, []);

  const t = useCallback((key: MessageKey) => MESSAGES[locale][key], [locale]);

  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext) ?? FALLBACK;
}
