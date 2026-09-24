/** Data hooks for the doctor portal. All types come from the generated API client. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap, type Schemas } from "@/lib/api";

export type Overview = Schemas["OverviewOut"];
export type Visit = Schemas["VisitOut"];
export type Note = Schemas["NoteOut"];
export type Prescription = Schemas["PrescriptionOut"];
export type Medication = Schemas["MedicationOut"];
export type TestOrder = Schemas["OrderOut"];
export type Report = Schemas["ReportOut"];
export type Document = Schemas["DocumentOut"];
export type Appointment = Schemas["AppointmentOut"];
export type FollowUp = Schemas["FollowUpOut"];
export type TimelineEvent = Schemas["TimelineEventOut"];
export type PatientListItem = Schemas["PatientListItem"];

const path = (patient_id: string) => ({ params: { path: { patient_id } } });

export const keys = {
  dashboard: ["doctor", "dashboard"] as const,
  patients: (q: string) => ["doctor", "patients", q] as const,
  patient: (pid: string) => ["patient", pid] as const,
  section: (pid: string, section: string) => ["patient", pid, section] as const,
};

// --- workspace -----------------------------------------------------------------------------

export function useDashboard() {
  return useQuery({
    queryKey: keys.dashboard,
    queryFn: () => unwrap(api.GET("/api/v1/doctor/dashboard")),
  });
}

export function usePatients(q: string) {
  return useQuery({
    queryKey: keys.patients(q),
    queryFn: () =>
      unwrap(api.GET("/api/v1/doctor/patients", { params: { query: q ? { q } : {} } })),
    placeholderData: (prev) => prev,
  });
}

export function useAddPatient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["NewPatientIn"]) => unwrap(api.POST("/api/v1/doctor/patients", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["doctor"] }),
  });
}

export function useConnectPatient() {
  return useMutation({
    mutationFn: (body: Schemas["ConnectIn"]) =>
      unwrap(api.POST("/api/v1/doctor/patients/connect", { body })),
  });
}

// --- chart reads ---------------------------------------------------------------------------

export function useOverview(pid: string) {
  return useQuery({
    queryKey: keys.section(pid, "overview"),
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/overview", path(pid))),
  });
}

export function useTimeline(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "timeline"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/timeline", path(pid))),
  });
}

export function useVisits(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "visits"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/visits", path(pid))),
  });
}

export function useVisit(pid: string, vid: string) {
  return useQuery({
    queryKey: [...keys.section(pid, "visits"), vid],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/patients/{patient_id}/visits/{visit_id}", {
          params: { path: { patient_id: pid, visit_id: vid } },
        }),
      ),
  });
}

export function useMedicalHistory(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "medical-history"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/medical-history", path(pid))),
  });
}

export function useMedications(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "medications"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/medications", path(pid))),
  });
}

export function useAdherence(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "adherence"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/adherence", path(pid))),
  });
}

export function usePrescriptions(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "prescriptions"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/prescriptions", path(pid))),
  });
}

export function useTestOrders(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "test-orders"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/test-orders", path(pid))),
  });
}

export function useReports(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "reports"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/reports", path(pid))),
  });
}

export function useDocuments(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "documents"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/documents", path(pid))),
  });
}

export function useAppointments(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "appointments"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/appointments", path(pid))),
  });
}

export function useFollowUps(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "follow-ups"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/follow-ups", path(pid))),
  });
}

// --- chart writes --------------------------------------------------------------------------

/** A mutation that refreshes the whole chart (and the dashboard) when it succeeds. */
function useChartMutation<TVars, TData>(pid: string, fn: (vars: TVars) => Promise<TData>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: keys.patient(pid) }),
        qc.invalidateQueries({ queryKey: keys.dashboard }),
      ]);
    },
  });
}

export function useRecordVisit(pid: string) {
  return useChartMutation(pid, (body: Schemas["VisitIn"]) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/visits", { ...path(pid), body })),
  );
}

export function useCompleteVisit(pid: string) {
  return useChartMutation(pid, (visit_id: string) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/visits/{visit_id}/complete", {
        params: { path: { patient_id: pid, visit_id } },
      }),
    ),
  );
}

export function useAddNote(pid: string) {
  return useChartMutation(pid, ({ visit_id, ...body }: Schemas["NoteIn"] & { visit_id: string }) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/visits/{visit_id}/notes", {
        params: { path: { patient_id: pid, visit_id } },
        body,
      }),
    ),
  );
}

export function useUpdateNote(pid: string) {
  return useChartMutation(pid, ({ note_id, ...body }: Schemas["NoteUpdate"] & { note_id: string }) =>
    unwrap(
      api.PUT("/api/v1/patients/{patient_id}/notes/{note_id}", {
        params: { path: { patient_id: pid, note_id } },
        body,
      }),
    ),
  );
}

export function useSignNote(pid: string) {
  return useChartMutation(pid, (note_id: string) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/notes/{note_id}/sign", {
        params: { path: { patient_id: pid, note_id } },
      }),
    ),
  );
}

export function useAmendNote(pid: string) {
  return useChartMutation(pid, ({ note_id, ...body }: Schemas["AmendmentIn"] & { note_id: string }) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/notes/{note_id}/amendments", {
        params: { path: { patient_id: pid, note_id } },
        body,
      }),
    ),
  );
}

export function useDocumentCondition(pid: string) {
  return useChartMutation(pid, (body: Schemas["ConditionIn"]) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/conditions", { ...path(pid), body })),
  );
}

export function useOrderTests(pid: string) {
  return useChartMutation(pid, (body: Schemas["OrderIn"]) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/test-orders", { ...path(pid), body })),
  );
}

export function useCancelOrder(pid: string) {
  return useChartMutation(pid, ({ order_id, reason }: { order_id: string; reason: string }) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/test-orders/{order_id}/cancel", {
        params: { path: { patient_id: pid, order_id } },
        body: { reason },
      }),
    ),
  );
}

export function useCreatePrescription(pid: string) {
  return useChartMutation(pid, (body: Schemas["PrescriptionIn"]) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/prescriptions", { ...path(pid), body })),
  );
}

export function useUpdatePrescription(pid: string) {
  return useChartMutation(
    pid,
    ({ prescription_id, ...body }: Schemas["PrescriptionUpdate"] & { prescription_id: string }) =>
      unwrap(
        api.PUT("/api/v1/patients/{patient_id}/prescriptions/{prescription_id}", {
          params: { path: { patient_id: pid, prescription_id } },
          body,
        }),
      ),
  );
}

export function useIssuePrescription(pid: string) {
  return useChartMutation(pid, (prescription_id: string) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/prescriptions/{prescription_id}/issue", {
        params: { path: { patient_id: pid, prescription_id } },
      }),
    ),
  );
}

export function useCancelPrescription(pid: string) {
  return useChartMutation(pid, ({ prescription_id, reason }: { prescription_id: string; reason: string }) =>
    unwrap(
      api.POST("/api/v1/patients/{patient_id}/prescriptions/{prescription_id}/cancel", {
        params: { path: { patient_id: pid, prescription_id } },
        body: { reason },
      }),
    ),
  );
}

export function useRecordMedication(pid: string) {
  return useChartMutation(pid, (body: Schemas["ExistingMedicationIn"]) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/medications", { ...path(pid), body })),
  );
}

export function useSetFollowUp(pid: string) {
  return useChartMutation(pid, (body: Schemas["FollowUpIn"]) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/follow-ups", { ...path(pid), body })),
  );
}

export function useCloseFollowUp(pid: string) {
  return useChartMutation(
    pid,
    ({ follow_up_id, outcome }: { follow_up_id: string; outcome: "complete" | "cancel" }) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/follow-ups/{follow_up_id}/{outcome}", {
          params: { path: { patient_id: pid, follow_up_id, outcome } },
        }),
      ),
  );
}

export function useBookAppointment(pid: string) {
  return useChartMutation(pid, (body: Schemas["AppointmentIn"]) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/appointments", { ...path(pid), body })),
  );
}

export function useRecordReport(pid: string) {
  return useChartMutation(pid, (body: Schemas["ReportIn"]) =>
    unwrap(api.POST("/api/v1/patients/{patient_id}/reports", { ...path(pid), body })),
  );
}

/** Presigned upload: ask the API, POST the file straight to storage, then confirm. */
export async function uploadDocument(
  pid: string,
  file: File,
  meta: { document_type: Schemas["DocumentType"]; title?: string; document_date?: string; visit_id?: string },
): Promise<Document> {
  const started = await unwrap(
    api.POST("/api/v1/patients/{patient_id}/documents/uploads", {
      ...path(pid),
      body: {
        document_type: meta.document_type,
        content_type: file.type,
        size_bytes: file.size,
        filename: file.name,
        title: meta.title || null,
        document_date: meta.document_date || null,
        visit_id: meta.visit_id || null,
      },
    }),
  );
  const form = new FormData();
  Object.entries(started.fields).forEach(([k, v]) => form.append(k, v));
  form.append("file", file);
  const upload = await fetch(started.upload_url, { method: "POST", body: form });
  if (!upload.ok) throw new Error("The file could not be uploaded to storage. Please try again.");
  return unwrap(
    api.POST("/api/v1/patients/{patient_id}/documents/{document_id}/complete", {
      params: { path: { patient_id: pid, document_id: started.document_id } },
    }),
  );
}

export async function documentDownloadUrl(pid: string, document_id: string): Promise<string> {
  const res = await unwrap(
    api.GET("/api/v1/patients/{patient_id}/documents/{document_id}/download", {
      params: { path: { patient_id: pid, document_id } },
    }),
  );
  return res.url;
}

// --- prescription documents and versions --------------------------------------------------------

export type PrescriptionDocument = Schemas["PrescriptionDocumentOut"];

export function usePrescriptionDocument(pid: string, prescription_id: string) {
  return useQuery({
    queryKey: [...keys.section(pid, "prescriptions"), prescription_id, "document"],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/patients/{patient_id}/prescriptions/{prescription_id}", {
          params: { path: { patient_id: pid, prescription_id } },
        }),
      ),
  });
}

/** Correct an issued prescription: creates a new draft version; the issued one is kept. */
export function useStartRevision(pid: string) {
  return useChartMutation(
    pid,
    ({ prescription_id, ...body }: Schemas["RevisionIn"] & { prescription_id: string }) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/prescriptions/{prescription_id}/revisions", {
          params: { path: { patient_id: pid, prescription_id } },
          body,
        }),
      ),
  );
}

/** Fetch the PDF with the session token and hand it to the browser as a download. */
export async function downloadPrescriptionPdf(pid: string, prescription_id: string, filename: string): Promise<void> {
  const { data, error, response } = await api.GET("/api/v1/patients/{patient_id}/prescriptions/{prescription_id}/pdf", {
    params: { path: { patient_id: pid, prescription_id } },
    parseAs: "blob",
  });
  if (error !== undefined || !data) {
    throw new Error(response.status === 403 ? "You do not have access to this prescription." : "The PDF could not be downloaded. Please try again.");
  }
  const url = URL.createObjectURL(data as Blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}
