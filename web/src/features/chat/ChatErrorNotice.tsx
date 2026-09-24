import { useState } from "react";
import { ApiError } from "@/shared/api/client";
import { useI18n } from "@/shared/i18n";
import { Alert, AlertDescription, Button } from "@/shared/ui";

export function ChatErrorNotice({
  error,
  fallback,
  correlationId,
  onRetry,
}: {
  error?: unknown;
  fallback?: string;
  correlationId?: string | null;
  onRetry?: () => void;
}) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const apiError = error instanceof ApiError ? error : null;
  const id = correlationId || apiError?.correlationId;
  const message = apiError?.message || fallback || t("chat_conversation_error");
  return (
    <Alert variant="destructive" role="alert">
      <AlertDescription className="flex flex-col gap-2">
        <span>{message}</span>
        <span className="text-xs">{t("chat_recovery_hint")}</span>
        {id && (
          <span className="flex flex-wrap items-center gap-2 break-all text-xs">
            {t("chat_correlation_id")}: {id}
            <Button
              type="button"
              size="xs"
              variant="outline"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(id);
                  setCopied(true);
                } catch {
                  setCopied(false);
                }
              }}
            >
              {copied ? t("chat_code_copied") : t("chat_code_copy")}
            </Button>
          </span>
        )}
        {onRetry && (
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            {t("retry")}
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}
