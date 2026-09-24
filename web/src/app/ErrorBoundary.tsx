import { Component, type ErrorInfo, type ReactNode } from "react";
import { useI18n } from "@/shared/i18n";
import { Alert, AlertDescription, Button } from "@/shared/ui";

/**
 * Confine per errori di rendering (B-03.2-26). Un crash in un componente
 * non deve lasciare la pagina bianca: mostra un fallback recuperabile senza
 * ricaricare l'intera app. `retry` azzera il confine e ritenta il render
 * degli stessi children; se l'errore è deterministico ricompare, ma
 * l'utente non resta bloccato senza un'azione esplicita.
 */
function ErrorFallback({ onRetry }: { onRetry: () => void }) {
  const { t } = useI18n();
  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-4 p-8">
      <h1 tabIndex={-1} className="text-2xl font-semibold outline-none">
        {t("error_title")}
      </h1>
      <Alert variant="destructive">
        <AlertDescription>{t("error_boundary_hint")}</AlertDescription>
      </Alert>
      <Button type="button" onClick={onRetry}>
        {t("retry")}
      </Button>
    </div>
  );
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Nessuna telemetria esterna (NewRay.md §19.4): solo la console locale,
    // utile in sviluppo e negli strumenti del browser dell'utente.
    console.error("ErrorBoundary", error, info.componentStack);
  }

  private handleRetry = (): void => {
    this.setState({ hasError: false });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return <ErrorFallback onRetry={this.handleRetry} />;
    }
    return this.props.children;
  }
}
