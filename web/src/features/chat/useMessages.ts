import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/shared/api/client";
import type { components } from "@/shared/contracts/api";

export type Message = components["schemas"]["MessageDTO"];
type MessageList = components["schemas"]["MessageListDTO"];

export function useMessages(conversationId: string | undefined) {
  return useInfiniteQuery({
    queryKey: ["messages", conversationId],
    queryFn: ({ pageParam }) =>
      apiFetch<MessageList>(
        `/api/v1/conversations/${conversationId}/messages?limit=200&after_sequence=${pageParam}`
      ),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => lastPage.next_sequence ?? undefined,
    enabled: !!conversationId,
  });
}

export function useInvalidateMessages() {
  const qc = useQueryClient();
  return (conversationId: string) =>
    qc.invalidateQueries({ queryKey: ["messages", conversationId] });
}
