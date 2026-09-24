/** Medication management hooks: schedules, pause/resume/stop, history, change requests. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { keys } from "@/features/chart/api";
import { api, unwrap, type Schemas } from "@/lib/api";

export type Med = Schemas["MedicationOut"];
export type ScheduleIn = Schemas["ScheduleIn"];
export type Check = Schemas["CheckOut"];
export type Advice = Schemas["AdviceIn"];
export type ChangeRequest = Schemas["ChangeRequestOut"];
export type MedEvent = Schemas["EventOut"];

const medPath = (patient_id: string, medication_id: string) => ({ params: { path: { patient_id, medication_id } } });

function useInvalidate(pid: string) {
  const qc = useQueryClient();
  return () => Promise.all([qc.invalidateQueries({ queryKey: keys.patient(pid) }), qc.invalidateQueries({ queryKey: ["me", "caregiving"] })]);
}

export function useMedication(pid: string, medication_id: string) {
  return useQuery({
    queryKey: [...keys.section(pid, "medications"), medication_id],
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/medications/{medication_id}", medPath(pid, medication_id))),
  });
}

export function useMedicationHistory(pid: string, medication_id: string) {
  return useQuery({
    queryKey: [...keys.section(pid, "medications"), medication_id, "history"],
    queryFn: () =>
      unwrap(api.GET("/api/v1/patients/{patient_id}/medications/{medication_id}/history", medPath(pid, medication_id))),
  });
}

/** What saving a schedule would need (nothing changes). */
export function checkSchedule(pid: string, medication_id: string, body: ScheduleIn): Promise<Check> {
  return unwrap(
    api.POST("/api/v1/patients/{patient_id}/medications/{medication_id}/schedule/check", { ...medPath(pid, medication_id), body }),
  );
}

export function useChangeSchedule(pid: string, medication_id: string) {
  const invalidate = useInvalidate(pid);
  return useMutation({
    mutationFn: (body: Schemas["ScheduleChangeIn"]) =>
      unwrap(api.PUT("/api/v1/patients/{patient_id}/medications/{medication_id}/schedule", { ...medPath(pid, medication_id), body })),
    onSuccess: invalidate,
  });
}

export function usePause(pid: string, medication_id: string) {
  const invalidate = useInvalidate(pid);
  return useMutation({
    mutationFn: (body: Schemas["PauseIn"]) =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/medications/{medication_id}/pause", { ...medPath(pid, medication_id), body })),
    onSuccess: invalidate,
  });
}

export function useResume(pid: string, medication_id: string) {
  const invalidate = useInvalidate(pid);
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/v1/patients/{patient_id}/medications/{medication_id}/resume", medPath(pid, medication_id))),
    onSuccess: invalidate,
  });
}

export function useStop(pid: string, medication_id: string) {
  const invalidate = useInvalidate(pid);
  return useMutation({
    mutationFn: (body: Schemas["StopIn"]) =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/medications/{medication_id}/stop", { ...medPath(pid, medication_id), body })),
    onSuccess: invalidate,
  });
}

export function useRequestChange(pid: string, medication_id: string) {
  const invalidate = useInvalidate(pid);
  return useMutation({
    mutationFn: (body: Schemas["ChangeRequestIn"]) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/medications/{medication_id}/change-requests", { ...medPath(pid, medication_id), body }),
      ),
    onSuccess: invalidate,
  });
}

export function useChangeRequests(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "medication-change-requests"),
    enabled,
    queryFn: () =>
      unwrap(api.GET("/api/v1/patients/{patient_id}/medication-change-requests", { params: { path: { patient_id: pid } } })),
  });
}

export function useWithdrawRequest(pid: string) {
  const invalidate = useInvalidate(pid);
  return useMutation({
    mutationFn: (request_id: string) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/medication-change-requests/{request_id}/withdraw", {
          params: { path: { patient_id: pid, request_id } },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useResolveRequest(pid: string) {
  const invalidate = useInvalidate(pid);
  return useMutation({
    mutationFn: ({ request_id, decision, note }: { request_id: string; decision: "approve" | "decline"; note: string | null }) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/medication-change-requests/{request_id}/{decision}", {
          params: { path: { patient_id: pid, request_id, decision } },
          body: { note },
        }),
      ),
    onSuccess: invalidate,
  });
}
