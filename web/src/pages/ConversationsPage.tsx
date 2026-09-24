import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useI18n } from "@/shared/i18n";
import { Button, Textarea } from "@/shared/ui";
import { createWorkspacePanelRegistry } from "@/shared/lib/workspacePanels";
import { WorkspacePanel } from "@/app/WorkspacePanel";
import {
  useConversations,
  useRenameConversation,
  useDeleteConversation,
  useMessages,
  useInvalidateMessages,
  useProfiles,
  useProvisionAssistant,
  useChat,
  createChatWorkspacePanels,
} from "@/features/chat";
import type { Message } from "@/features/chat";
import { MessageContent } from "@/features/chat/MessageContent";
import { ChatErrorNotice } from "@/features/chat/ChatErrorNotice";

function MessageList({
  messages,
  streamedText,
  streaming,
  hasMore,
  loadingMore,
  onLoadMore,
}: {
  messages: Message[];
  streamedText: string;
  streaming: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}) {
  const { t } = useI18n();
  const endRef = useRef<HTMLDivElement>(null);
  const persistedReply = messages.at(-1);
  const showStreamedText =
    streamedText &&
    (streaming || persistedReply?.role !== "assistant" || persistedReply.content !== streamedText);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, streamedText]);

  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4" role="log" aria-live="polite">
      {hasMore && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={loadingMore}
          onClick={onLoadMore}
        >
          {loadingMore ? t("chat_messages_loading_more") : t("chat_messages_load_more")}
        </Button>
      )}
      {messages.length === 0 && !streamedText && (
        <p className="text-sm text-muted-foreground">{t("chat_messages_empty")}</p>
      )}
      {messages.map((m) => (
        <div
          key={m.id}
          className={`max-w-[80%] rounded-lg px-4 py-2 text-sm ${
            m.role === "user" ? "ml-auto bg-primary text-primary-foreground" : "mr-auto bg-muted"
          }`}
          data-role={m.role}
          data-sequence={m.sequence}
        >
          <MessageContent content={m.content} />
        </div>
      ))}
      {showStreamedText && (
        <div
          className="mr-auto max-w-[80%] rounded-lg bg-muted px-4 py-2 text-sm"
          data-role="assistant"
          data-streaming={streaming ? "true" : "false"}
        >
          <MessageContent content={streamedText} />
          {streaming && <span className="ml-1 inline-block animate-pulse">|</span>}
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}

function TokenRateMeter({
  label,
  value,
  status,
}: {
  label: string;
  value: string;
  status: string;
}) {
  if (status === "idle") return null;
  return (
    <span
      className="text-xs text-muted-foreground tabular-nums"
      data-testid="token-rate"
      aria-label={label}
    >
      {value}
    </span>
  );
}

function ChatInput({
  onSend,
  onStop,
  streaming,
  disabled,
}: {
  onSend: (content: string) => void;
  onStop: () => void;
  streaming: boolean;
  disabled: boolean;
}) {
  const { t } = useI18n();
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = value.trim();
      if (!trimmed || streaming || disabled) return;
      onSend(trimmed);
      setValue("");
    },
    [value, streaming, disabled, onSend]
  );

  useEffect(() => {
    if (!streaming) inputRef.current?.focus();
  }, [streaming]);

  return (
    <form onSubmit={handleSubmit} className="border-t border-border p-4">
      <div className="flex items-end gap-2">
        <Textarea
          ref={inputRef}
          rows={2}
          maxLength={50_000}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={t("chat_placeholder")}
          disabled={streaming || disabled}
          aria-label={t("chat_placeholder")}
          aria-describedby="chat-composer-hint"
          className="flex-1"
        />
        {streaming ? (
          <Button type="button" variant="destructive" onClick={onStop}>
            {t("chat_stop")}
          </Button>
        ) : (
          <Button type="submit" disabled={!value.trim() || disabled}>
            {t("chat_send")}
          </Button>
        )}
      </div>
      <p id="chat-composer-hint" className="mt-1 text-xs text-muted-foreground">
        {t("chat_composer_hint")}
      </p>
    </form>
  );
}

export function ConversationsPage() {
  const { t } = useI18n();
  const { id: activeId } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const conversations = useConversations();
  const renameConversation = useRenameConversation();
  const deleteConversation = useDeleteConversation();
  const messagesQuery = useMessages(activeId);
  const invalidateMessages = useInvalidateMessages();
  const profiles = useProfiles();
  const provisionAssistant = useProvisionAssistant();
  const chat = useChat(activeId);

  const [conversationError, setConversationError] = useState<unknown>(null);
  const assistant = profiles.data?.items.find((profile) => profile.kind === "assistant");
  const selectedProfileId = assistant?.model ? assistant.id : undefined;

  const handleSend = useCallback(
    (content: string) => {
      if (!activeId || !selectedProfileId) return;
      chat.send(activeId, selectedProfileId, content).then(() => {
        invalidateMessages(activeId);
      });
    },
    [activeId, selectedProfileId, chat, invalidateMessages]
  );

  const handleRename = useCallback(
    (id: string, title: string, expectedUpdatedAt: string) => {
      setConversationError(null);
      renameConversation.mutate(
        { id, title, expectedUpdatedAt },
        {
          onError: (error) => {
            setConversationError(error);
          },
        }
      );
    },
    [renameConversation]
  );

  const handleDelete = useCallback(
    (id: string, expectedUpdatedAt: string) => {
      setConversationError(null);
      deleteConversation.mutate(
        { id, expectedUpdatedAt },
        {
          onSuccess: () => {
            if (id === activeId) navigate("/conversations");
          },
          onError: (error) => {
            setConversationError(error);
          },
        }
      );
    },
    [deleteConversation, activeId, navigate]
  );

  const items = conversations.data?.pages.flatMap((page) => page.items) ?? [];
  const messages = messagesQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const workspacePanels = createWorkspacePanelRegistry(
    createChatWorkspacePanels({
      conversationsLoading: conversations.isPending,
      conversationsError: conversations.error,
      onRetryConversations: () => void conversations.refetch(),
      conversations: items,
      activeId,
      hasNextPage: !!conversations.hasNextPage,
      loadingMore: conversations.isFetchingNextPage,
      onSelectConversation: (id) => navigate(`/conversations/${id}`),
      onRenameConversation: handleRename,
      onDeleteConversation: handleDelete,
      onLoadMore: () => void conversations.fetchNextPage(),
    })
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-3">
      <h1 tabIndex={-1} className="text-2xl font-semibold outline-none">
        {t("conversations_title")}
      </h1>

      {conversationError != null && <ChatErrorNotice error={conversationError} />}

      {!profiles.isPending && !profiles.isError && !assistant && (
        <div className="flex flex-col gap-2 rounded-lg border border-border p-4">
          <p className="text-sm text-muted-foreground">{t("chat_assistant_setup_hint")}</p>
          <Button
            type="button"
            className="self-start"
            disabled={provisionAssistant.isPending}
            onClick={() => provisionAssistant.mutate()}
          >
            {provisionAssistant.isPending ? t("loading") : t("chat_assistant_setup")}
          </Button>
          {provisionAssistant.isError && (
            <ChatErrorNotice
              error={provisionAssistant.error}
              fallback={t("chat_assistant_provision_error")}
            />
          )}
        </div>
      )}
      {profiles.isError && (
        <ChatErrorNotice error={profiles.error} onRetry={() => void profiles.refetch()} />
      )}
      {assistant && !assistant.model && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("chat_no_model")}
        </p>
      )}

      <div className="flex min-h-[60vh] min-w-0 gap-4">
        <div className="flex min-w-0 flex-1 flex-col rounded-lg border border-border">
          {activeId ? (
            <>
              <div className="flex min-h-12 items-center justify-end gap-3 border-b border-border px-4 py-2">
                <TokenRateMeter
                  label={t("chat_token_rate")}
                  value={chat.tokenRate}
                  status={chat.status}
                />
                {chat.status !== "idle" && chat.status !== "streaming" && (
                  <span className="text-xs text-muted-foreground" data-testid="run-status">
                    {t(`chat_status_${chat.status}`)}
                  </span>
                )}
              </div>
              {messagesQuery.isError && (
                <ChatErrorNotice
                  error={messagesQuery.error}
                  onRetry={() => void messagesQuery.refetch()}
                />
              )}
              {messagesQuery.isPending && (
                <p role="status" className="p-4">
                  {t("loading")}
                </p>
              )}
              {!messagesQuery.isPending && !messagesQuery.isError && (
                <MessageList
                  messages={messages}
                  streamedText={chat.text}
                  streaming={chat.streaming}
                  hasMore={!!messagesQuery.hasNextPage}
                  loadingMore={messagesQuery.isFetchingNextPage}
                  onLoadMore={() => void messagesQuery.fetchNextPage()}
                />
              )}
              {chat.error && (
                <div className="mx-4 mb-2">
                  <ChatErrorNotice fallback={chat.error} correlationId={chat.correlationId} />
                </div>
              )}
              <ChatInput
                onSend={handleSend}
                onStop={chat.stop}
                streaming={chat.streaming}
                disabled={!selectedProfileId}
              />
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-muted-foreground">
              <p>{t("chat_select")}</p>
            </div>
          )}
        </div>
        <WorkspacePanel panels={workspacePanels} />
      </div>
    </div>
  );
}
