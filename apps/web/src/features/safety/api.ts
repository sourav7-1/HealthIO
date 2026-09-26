/** Medication safety warnings: list, re-check, review/acknowledge, prescription preview. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { keys } from "@/features/chart/api";
import { api, unwrap, type Schemas } from "@/lib/api";

export type SafetyWarning = Schemas["WarningOut"];
export type SafetyFinding = Schemas["SafetyFindingOut"];
export type SafetyCheck = Schemas["SafetyCheckOut"];

export const HEADLINE = "Potential issue detected. Please confirm with a doctor/pharmacist.";

const section = (pid: string) => keys.section(pid, "safety-warnings");

export function useSafetyWarnings(pid: string, includeResolved = false, enabled = true) {
  return useQuery({
    queryKey: [...section(pid), includeResolved],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/patients/{patient_id}/safety-warnings", {
          params: { path: { patient_id: pid }, query: { include_resolved: includeResolved } },
        }),
      ),
  });
}

export function useReferenceDatasets() {
  return useQuery({
    queryKey: ["safety", "reference-datasets"],
    queryFn: () => unwrap(api.GET("/api/v1/safety/reference-datasets")),
    staleTime: 5 * 60_000,
  });
}

export function useReviewWarning(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ warning_id, note }: { warning_id: string; note?: string }) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/safety-warnings/{warning_id}/review", {
          params: { path: { patient_id: pid, warning_id } },
          body: { note: note ?? null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: section(pid) }),
  });
}

export function useRecheck(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/safety-warnings/recheck", { params: { path: { patient_id: pid } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: section(pid) }),
  });
}

/** Doctor: check a (draft) prescription before issuing. Nothing is stored. */
export function usePrescriptionSafetyCheck(pid: string, prescriptionId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...section(pid), "preview", prescriptionId],
    enabled,
    staleTime: 0,
    queryFn: () =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/prescriptions/{prescription_id}/safety-check", {
          params: { path: { patient_id: pid, prescription_id: prescriptionId } },
        }),
      ),
  });
}
