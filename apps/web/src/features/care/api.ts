/** Data hooks for the caregiver portal: people I look after, invitations, dependants. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "@/lib/api";

export type CareLink = Schemas["CaregiverLinkOut"];
export type PersonSummary = Schemas["DependantSummary"];

const CARE = ["me", "caregiving"] as const;

export function useCaregiving() {
  return useQuery({ queryKey: [...CARE, "links"], queryFn: () => unwrap(api.GET("/api/v1/me/caregiving")) });
}

export function useCareDashboard() {
  return useQuery({
    queryKey: [...CARE, "dashboard"],
    queryFn: () => unwrap(api.GET("/api/v1/me/caregiving/dashboard")),
    refetchInterval: 60_000, // dose statuses change with time
  });
}

function useInvalidateCare() {
  const qc = useQueryClient();
  // Roles can change too (accepting makes you a caregiver), so refresh "me" as well.
  return () => Promise.all([qc.invalidateQueries({ queryKey: CARE }), qc.invalidateQueries({ queryKey: ["me"], exact: true })]);
}

export function useRespondToInvitation() {
  const invalidate = useInvalidateCare();
  return useMutation({
    mutationFn: ({ relationship_id, decision }: { relationship_id: string; decision: "accept" | "decline" }) =>
      decision === "accept"
        ? unwrap(api.POST("/api/v1/caregiver-invitations/{relationship_id}/accept", { params: { path: { relationship_id } } }))
        : unwrap(api.POST("/api/v1/caregiver-invitations/{relationship_id}/decline", { params: { path: { relationship_id } } })),
    onSuccess: invalidate,
  });
}

export function useLeave() {
  const invalidate = useInvalidateCare();
  return useMutation({
    mutationFn: (relationship_id: string) =>
      unwrap(api.POST("/api/v1/me/caregiving/{relationship_id}/leave", { params: { path: { relationship_id } } })),
    onSuccess: invalidate,
  });
}

export function useCreateDependant() {
  const invalidate = useInvalidateCare();
  return useMutation({
    mutationFn: (body: Schemas["DependantIn"]) => unwrap(api.POST("/api/v1/me/dependants", { body })),
    onSuccess: invalidate,
  });
}
