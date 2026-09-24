/** Prescription scans: upload a photo, let AI read it (with consent) or type it in, review, save. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { keys, uploadDocument } from "@/features/chart/api";
import { api, unwrap, type Schemas } from "@/lib/api";

export type Scan = Schemas["ScanOut"];
export type ScanField = Schemas["ScanFieldOut"];
export type ScanItem = Schemas["ScanItemOut"];
export type ReviewOp = Schemas["ReviewOpIn"];

const scanKey = (pid: string, id: string) => [...keys.section(pid, "prescription-scans"), id] as const;

export function useAiConsent(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "ai-consent"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/ai-consent", { params: { path: { patient_id: pid } } })),
  });
}

export function useSetAiConsent(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (granted: boolean) =>
      unwrap(api.PUT("/api/v1/patients/{patient_id}/ai-consent", { params: { path: { patient_id: pid } }, body: { granted } })),
    onSuccess: (data) => qc.setQueryData(keys.section(pid, "ai-consent"), data),
  });
}

export function useScans(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "prescription-scans"),
    enabled,
    queryFn: () =>
      unwrap(api.GET("/api/v1/patients/{patient_id}/prescription-scans", { params: { path: { patient_id: pid } } })),
  });
}

export function useScan(pid: string, scan_id: string) {
  return useQuery({
    queryKey: scanKey(pid, scan_id),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/patients/{patient_id}/prescription-scans/{scan_id}", {
          params: { path: { patient_id: pid, scan_id } },
        }),
      ),
    // Keep checking while the AI is still reading (worker mode).
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 2000 : false),
  });
}

/** Upload the photo as a prescription document, then start reading it. */
export async function startScanFromPhoto(pid: string, file: File, mode: "ai" | "manual"): Promise<Scan> {
  const doc = await uploadDocument(pid, file, { document_type: "prescription", title: "Prescription photo" });
  return unwrap(
    api.POST("/api/v1/patients/{patient_id}/prescription-scans", {
      params: { path: { patient_id: pid } },
      body: { document_id: doc.id, mode },
    }),
  );
}

export function useStartScan(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { document_id: string; mode: "ai" | "manual" }) =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/prescription-scans", { params: { path: { patient_id: pid } }, body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.section(pid, "prescription-scans") }),
  });
}

export function useReviewScan(pid: string, scan_id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ops: ReviewOp[]) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/prescription-scans/{scan_id}/review", {
          params: { path: { patient_id: pid, scan_id } },
          body: { ops },
        }),
      ),
    onSuccess: (data) => qc.setQueryData(scanKey(pid, scan_id), data),
  });
}

export function useConfirmScan(pid: string, scan_id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/prescription-scans/{scan_id}/confirm", {
          params: { path: { patient_id: pid, scan_id } },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.patient(pid) }),
  });
}

export function useRejectScan(pid: string, scan_id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reason: string) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/prescription-scans/{scan_id}/reject", {
          params: { path: { patient_id: pid, scan_id } },
          body: { reason },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.patient(pid) }),
  });
}
