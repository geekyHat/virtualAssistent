import { useCallback, useEffect, useState } from "react";
import { MessagesSquare } from "lucide-react";
import { useI18n } from "@/shared/i18n";
import { Button, Input } from "@/shared/ui";
import type { WorkspacePanelDefinition } from "@/shared/lib/workspacePanels";
import type { Conversation } from "./useConversations";
import { ChatErrorNotice } from "./ChatErrorNotice";

export interface ChatWorkspacePanelProps {
  conversationsLoading: boolean;
  conversationsError: unknown;
  onRetryConversations: () => void;
  conversations: Conversation[];
  activeId: string | undefined;
  hasNextPage: boolean;
  loadingMore: boolean;
  onSelectConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string, expectedUpdatedAt: string) => void;
  onDeleteConversation: (id: string, expectedUpdatedAt: string) => void;
  onLoadMore: () => void;
}

function ConversationItem({
  conversation,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  conversation: Conversation;
  active: boolean;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string, expectedUpdatedAt: string) => void;
  onDelete: (id: string, expectedUpdatedAt: string) => void;
}) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(conversation.title);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => {
    if (!editing) setTitle(conversation.title);
  }, [conversation.title, editing]);

  const commitRename = useCallback(() => {
    setEditing(false);
    const trimmed = title.trim();
    if (trimmed && trimmed !== conversation.title) {
      onRename(conversation.id, trimmed, conversation.updated_at);
    } else {
      setTitle(conversation.title);
    }
  }, [conversation.id, conversation.title, conversation.updated_at, onRename, title]);

  if (editing) {
    return (
      <li>
        <Input
          autoFocus
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onBlur={commitRename}
          onKeyDown={(event) => {
            if (event.key === "Enter") commitRename();
            if (event.key === "Escape") {
              setTitle(conversation.title);
              setEditing(false);
            }
          }}
          aria-label={`${t("chat_rename")} ${conversation.title}`}
        />
      </li>
    );
  }

  return (
    <li className="flex min-w-0 items-center gap-1">
      <button
        type="button"
        className={`min-w-0 flex-1 truncate rounded-md px-2 py-2 text-left text-sm ${active ? "bg-accent font-medium text-accent-foreground" : "hover:bg-accent/60"}`}
        onClick={() => onSelect(conversation.id)}
        onDoubleClick={() => setEditing(true)}
        aria-current={active ? "page" : undefined}
      >
        {conversation.title}
      </button>
      {confirmingDelete ? (
        <>
          <button
            type="button"
            className="shrink-0 rounded bg-destructive px-1.5 py-1 text-xs text-white"
            onClick={() => {
              setConfirmingDelete(false);
              onDelete(conversation.id, conversation.updated_at);
            }}
            aria-label={`${t("chat_delete_confirm")} ${conversation.title}`}
          >
            {t("chat_delete_confirm")}
          </button>
          <button
            type="button"
            className="shrink-0 rounded p-1 text-xs text-muted-foreground"
            onClick={() => setConfirmingDelete(false)}
            aria-label={t("chat_delete_cancel")}
          >
            {t("chat_delete_cancel")}
          </button>
        </>
      ) : (
        <>
          <button
            type="button"
            className="shrink-0 rounded p-1 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setEditing(true)}
            aria-label={`${t("chat_rename")} ${conversation.title}`}
          >
            {t("chat_rename_icon")}
          </button>
          <button
            type="button"
            className="shrink-0 rounded p-1 text-xs text-muted-foreground hover:text-destructive"
            onClick={() => setConfirmingDelete(true)}
            aria-label={`${t("chat_delete")} ${conversation.title}`}
          >
            &times;
          </button>
        </>
      )}
    </li>
  );
}

function ConversationsPanel(props: ChatWorkspacePanelProps) {
  const { t } = useI18n();
  if (props.conversationsLoading)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("loading")}
      </p>
    );
  if (props.conversationsError)
    return (
      <ChatErrorNotice error={props.conversationsError} onRetry={props.onRetryConversations} />
    );
  return (
    <>
      {props.conversations.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("chat_empty")}</p>
      ) : (
        <ul className="flex flex-col gap-1" role="list" aria-label={t("chat_conversation_list")}>
          {props.conversations.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              conversation={conversation}
              active={conversation.id === props.activeId}
              onSelect={props.onSelectConversation}
              onRename={props.onRenameConversation}
              onDelete={props.onDeleteConversation}
            />
          ))}
        </ul>
      )}
      {props.hasNextPage && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="mt-2 w-full"
          onClick={props.onLoadMore}
          disabled={props.loadingMore}
        >
          {props.loadingMore
            ? t("chat_conversations_loading_more")
            : t("chat_conversations_load_more")}
        </Button>
      )}
    </>
  );
}

export function createChatWorkspacePanels(
  props: ChatWorkspacePanelProps
): WorkspacePanelDefinition[] {
  return [
    {
      id: "conversations",
      route: "/conversations",
      capability: "conversations.read",
      labelKey: "nav_conversations",
      icon: MessagesSquare,
      order: 10,
      available: true,
      render: () => <ConversationsPanel {...props} />,
    },
  ];
}
