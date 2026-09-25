/** Tests and reports: report detail, uploads by patients, review, status and sharing. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { keys, uploadDocument } from "@/features/chart/api";
import { api, unwrap, type Schemas } from "@/lib/api";
import { humanize } from "@/lib/format";

export type ReportDetail = Schemas["ReportDetailOut"];
export type ReportSummary = Schemas["ReportOut"];
export type OrderStatus = Schemas["TestOrderStatus"];

/** Report files: PDF, JPEG or PNG, up to 15 MB (the API checks again, and scans them). */
export const REPORT_FILE_TYPES = ["application/pdf", "image/jpeg", "image/png"];
export const REPORT_ACCEPT = REPORT_FILE_TYPES.join(",");
export const MAX_FILE_BYTES = 15 * 1024 * 1024;

export function checkReportFile(file: File): string | null {
  if (!REPORT_FILE_TYPES.includes(file.type)) return "Reports must be a PDF, JPG or PNG file.";
  if (file.size > MAX_FILE_BYTES) return "Files must be smaller than 15 MB.";
  if (file.size === 0) return "This file is empty.";
  return null;
}

const SOURCE: Record<string, string> = {
  doctor: "Recorded by a doctor",
  patient: "Uploaded by the patient",
  caregiver: "Uploaded by a caregiver",
  integration: "From a lab system",
  ai_extraction: "Read from a photo and checked",
};

export function sourceLabel(source: string, self: boolean): string {
  if (self && source === "patient") return "Uploaded by you";
  return SOURCE[source] ?? humanize(source);
}

const reportPath = (patient_id: string, report_id: string) => ({ params: { path: { patient_id, report_id } } });

export function useReport(pid: string, reportId: string | null) {
  return useQuery({
    queryKey: [...keys.section(pid, "reports"), reportId],
    enabled: reportId !== null,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/reports/{report_id}", reportPath(pid, reportId!))),
  });
}

/** A 60-second link to the report file (also works for a report shared with you). */
export async function reportFileUrl(pid: string, reportId: string): Promise<string> {
  return (await unwrap(api.GET("/api/v1/patients/{patient_id}/reports/{report_id}/file", reportPath(pid, reportId)))).url;
}

function useReportMutation<TVars, TData>(pid: string, fn: (vars: TVars) => Promise<TData>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: async () => {
      await Promise.all([qc.invalidateQueries({ queryKey: keys.patient(pid) }), qc.invalidateQueries({ queryKey: keys.dashboard })]);
    },
  });
}

export interface UploadedReport {
  file: File;
  order_id: string | null;
  test_name: string | null;
  report_date: string | null;
  lab_name: string | null;
  lab_reference: string | null;
  notes: string | null;
}

/** Patient or caregiver: upload the file (checked and scanned), then add it as a report. */
export function useUploadReport(pid: string) {
  return useReportMutation(pid, async ({ file, ...meta }: UploadedReport) => {
    const doc = await uploadDocument(pid, file, {
      document_type: "lab_report",
      title: meta.test_name ?? undefined,
      document_date: meta.report_date ?? undefined,
    });
    return unwrap(
      api.POST("/api/v1/patients/{patient_id}/reports/uploaded", {
        params: { path: { patient_id: pid } },
        body: { document_id: doc.id, ...meta },
      }),
    );
  });
}

export function useReviewReport(pid: string) {
  return useReportMutation(pid, ({ report_id, ...body }: Schemas["ReportReviewIn"] & { report_id: string }) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/reports/{report_id}/review", { ...reportPath(pid, report_id), body })),
  );
}

export function useReportInError(pid: string) {
  return useReportMutation(pid, ({ report_id, reason }: { report_id: string; reason: string }) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/reports/{report_id}/entered-in-error", {
        ...reportPath(pid, report_id),
        body: { reason },
      }),
    ),
  );
}

export function useShareReport(pid: string) {
  return useReportMutation(pid, ({ report_id, ...body }: Schemas["ShareIn"] & { report_id: string }) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/reports/{report_id}/shares", { ...reportPath(pid, report_id), body })),
  );
}

export function useRevokeShare(pid: string) {
  return useReportMutation(pid, (share_id: string) =>
    unwrap(
      api.DELETE("/api/v1/patients/{patient_id}/report-shares/{share_id}", {
        params: { path: { patient_id: pid, share_id } },
      }),
    ),
  );
}

export function useOrderStatus(pid: string) {
  return useReportMutation(pid, ({ order_id, ...body }: Schemas["OrderStatusIn"] & { order_id: string }) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/test-orders/{order_id}/status", {
        params: { path: { patient_id: pid, order_id } },
        body,
      }),
    ),
  );
}
