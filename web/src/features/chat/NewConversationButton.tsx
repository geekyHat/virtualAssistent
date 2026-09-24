import { useState } from "react";
import { Plus } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "@/shared/api/client";
import { useI18n } from "@/shared/i18n";
import { cn } from "@/shared/lib/utils";
import { useConversations, useCreateConversation } from "./useConversations";

export function NewConversationButton() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const conversations = useConversations();
  const createConversation = useCreateConversation();
  const [error, setError] = useState<string | null>(null);
  const count =
    conversations.data?.pages.reduce((total, page) => total + page.items.length, 0) ?? 0;

  return (
    <div>
      <button
        type="button"
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-medium",
          "text-foreground transition-colors hover:bg-accent/60 focus-visible:outline-none",
          "focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50"
        )}
        disabled={createConversation.isPending || conversations.isPending}
        onClick={() => {
          setError(null);
          createConversation.mutate(`${t("chat_new_title")} ${count + 1}`, {
            onSuccess: (conversation) => navigate(`/conversations/${conversation.id}`),
            onError: (reason) =>
              setError(reason instanceof ApiError ? reason.message : t("chat_conversation_error")),
          });
        }}
      >
        <Plus aria-hidden="true" className="size-4" />
        {t("chat_new")}
      </button>
      {error && (
        <p role="alert" className="px-3 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
