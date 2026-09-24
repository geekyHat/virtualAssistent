import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/shared/api/client";

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface ConversationList {
  items: Conversation[];
  next_cursor: string | null;
}

export function useConversations() {
  return useInfiniteQuery({
    queryKey: ["conversations"],
    queryFn: ({ pageParam }) =>
      apiFetch<ConversationList>(
        `/api/v1/conversations${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (title: string) =>
      apiFetch<Conversation>("/api/v1/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

export function useRenameConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      title,
      expectedUpdatedAt,
    }: {
      id: string;
      title: string;
      expectedUpdatedAt: string;
    }) =>
      apiFetch<Conversation>(`/api/v1/conversations/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, expected_updated_at: expectedUpdatedAt }),
      }),
    onSettled: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, expectedUpdatedAt }: { id: string; expectedUpdatedAt: string }) =>
      apiFetch<void>(
        `/api/v1/conversations/${id}?expected_updated_at=${encodeURIComponent(expectedUpdatedAt)}`,
        { method: "DELETE" }
      ),
    onSuccess: (_data, { id }) => {
      qc.removeQueries({ queryKey: ["messages", id] });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
}
