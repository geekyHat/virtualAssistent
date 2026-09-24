import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/shared/api/client";
import type { Profile } from "./useProfiles";

export interface SwitchProfileModelInput {
  profileId: string;
  modelName: string;
  expectedProfileVersion: string;
}

export function useSwitchProfileModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId, modelName, expectedProfileVersion }: SwitchProfileModelInput) =>
      apiFetch<Profile>(`/api/v1/profiles/${profileId}/versions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model_name: modelName,
          expected_profile_version: expectedProfileVersion,
          idempotency_key: crypto.randomUUID(),
        }),
      }),
    onSettled: () => {
      // Sia in caso di successo sia di 409: la lista dei profili deve
      // riflettere lo stato reale del server, mai quello atteso dal client.
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}
