import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/shared/api/client";
import type { components } from "@/shared/contracts/api";

export type Profile = components["schemas"]["ProfileDTO"];
type ProfileList = components["schemas"]["ProfileListDTO"];

export function useProfiles() {
  return useQuery({
    queryKey: ["profiles"],
    queryFn: () => apiFetch<ProfileList>("/api/v1/profiles"),
  });
}

export function useProvisionAssistant() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<Profile>("/api/v1/profiles/defaults", { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["profiles"] }),
  });
}
