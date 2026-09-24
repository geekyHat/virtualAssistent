import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/shared/api/client";
import type { components } from "@/shared/contracts/api";

export type RuntimeModel = components["schemas"]["ModelInfoDTO"];
type ModelList = components["schemas"]["ModelListDTO"];
export type ModelReadiness = components["schemas"]["ModelReadinessDTO"];

export function useModels() {
  return useQuery({
    queryKey: ["models"],
    queryFn: () => apiFetch<ModelList>("/api/v1/models"),
  });
}

export function useModelReadiness() {
  return useQuery({
    queryKey: ["models", "readiness"],
    queryFn: () => apiFetch<ModelReadiness>("/api/v1/models/readiness"),
    staleTime: 10_000,
  });
}
