/** Medical record timeline: paginated, filtered events, per-record history, symptoms. */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { keys } from "@/features/chart/api";
import { api, unwrap, type Schemas } from "@/lib/api";

export type TimelineEvent = Schemas["TimelineEventOut"];
export type TimelinePage = Schemas["TimelinePageOut"];
export type TimelineKind = TimelineEvent["kind"];
export type HistoryEntry = Schemas["HistoryEntryOut"];
export type Symptom = Schemas["SymptomOut"];

export interface TimelineFilters {
  kinds: TimelineKind[];
  dateFrom: string;
  dateTo: string;
  doctorId: string;
  specialty: string;
  order: "newest" | "oldest";
}

export const NO_FILTERS: TimelineFilters = { kinds: [], dateFrom: "", dateTo: "", doctorId: "", specialty: "", order: "newest" };

export function filterCount(f: TimelineFilters): number {
  return (f.kinds.length ? 1 : 0) + (f.dateFrom ? 1 : 0) + (f.dateTo ? 1 : 0) + (f.doctorId ? 1 : 0) + (f.specialty ? 1 : 0);
}

function query(f: TimelineFilters, cursor: string | null, limit: number) {
  return {
    kind: f.kinds.length ? f.kinds : undefined,
    date_from: f.dateFrom || undefined,
    date_to: f.dateTo || undefined,
    doctor_id: f.doctorId || undefined,
    specialty: f.specialty || undefined,
    order: f.order,
    cursor: cursor ?? undefined,
    limit,
  };
}

/** Pages of events. The server applies permissions, consent and filters. */
export function useTimeline(pid: string, filters: TimelineFilters = NO_FILTERS, limit = 30) {
  return useInfiniteQuery({
    queryKey: [...keys.section(pid, "timeline"), filters, limit],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/patients/{patient_id}/timeline", {
          params: { path: { patient_id: pid }, query: query(filters, pageParam, limit) },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor,
    placeholderData: (prev) => prev,
  });
}

/** The latest few events (dashboards). */
export function useRecentActivity(pid: string, limit = 6, enabled = true) {
  return useQuery({
    queryKey: [...keys.section(pid, "timeline"), "recent", limit],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/patients/{patient_id}/timeline", {
          params: { path: { patient_id: pid }, query: { limit } },
        }),
      ),
  });
}

export function useRecordHistory(pid: string, kind: TimelineKind, resourceId: string, enabled = true) {
  return useQuery({
    queryKey: [...keys.section(pid, "timeline"), "history", kind, resourceId],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/patients/{patient_id}/timeline/{kind}/{resource_id}/history", {
          params: { path: { patient_id: pid, kind, resource_id: resourceId } },
        }),
      ),
  });
}

export function useSymptoms(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "symptoms"),
    enabled,
    queryFn: () =>
      unwrap(api.GET("/api/v1/patients/{patient_id}/symptoms", { params: { path: { patient_id: pid } } })),
  });
}

function useRecordMutation<TVars, TData>(pid: string, fn: (vars: TVars) => Promise<TData>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.patient(pid) }),
  });
}

/** Patient or caregiver: a symptom in their own words. */
export function useReportSymptom(pid: string) {
  return useRecordMutation(pid, (body: Schemas["SymptomIn"]) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/symptoms", { params: { path: { patient_id: pid } }, body })),
  );
}

/** Doctor: symptoms as the patient presented them. */
export function useDocumentSymptom(pid: string) {
  return useRecordMutation(pid, (body: Schemas["DocumentedSymptomIn"]) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/symptoms/documented", { params: { path: { patient_id: pid } }, body }),
    ),
  );
}

export function useCorrectSymptom(pid: string) {
  return useRecordMutation(pid, ({ symptom_id, ...body }: Schemas["SymptomCorrectionIn"] & { symptom_id: string }) =>
    unwrap(
      api.PATCH("/api/v1/patients/{patient_id}/symptoms/{symptom_id}", {
        params: { path: { patient_id: pid, symptom_id } },
        body,
      }),
    ),
  );
}
