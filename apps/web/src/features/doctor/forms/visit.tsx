import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router";
import { z } from "zod";

import { Alert, Button, Dialog, Field, Input, Select, Textarea } from "@/components/ui";
import { todayIso } from "@/lib/format";

import { useDocumentCondition, useRecordVisit } from "@/features/chart/api";
import { useSubmit } from "@/features/chart/useSubmit";

// --- Record visit --------------------------------------------------------------------------

const visitSchema = z.object({
  visit_type: z.enum(["in_person", "teleconsult", "home_visit", "emergency"]),
  chief_complaint: z.string().trim().max(2000).optional(),
  location: z.string().trim().max(200).optional(),
});
type VisitValues = z.infer<typeof visitSchema>;

export function RecordVisitDialog({
  patientId,
  open,
  onClose,
}: {
  patientId: string;
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const record = useRecordVisit(patientId);
  const { register, handleSubmit, reset, setError, formState } = useForm<VisitValues>({
    resolver: zodResolver(visitSchema),
    defaultValues: { visit_type: "in_person" },
  });
  const { run, formError, clearFormError } = useSubmit(setError);
  const close = () => {
    reset();
    clearFormError();
    onClose();
  };

  const onSubmit = handleSubmit(async (v) => {
    let visitId: string | null = null;
    const ok = await run(async () => {
      const visit = await record.mutateAsync({
        visit_type: v.visit_type,
        chief_complaint: v.chief_complaint || null,
        location: v.location || null,
      });
      visitId = visit.id;
    }, "Visit started");
    if (ok && visitId) {
      close();
      navigate(`/doctor/patients/${patientId}/visits/${visitId}`);
    }
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Record a visit"
      description="Starts a visit now. You can add notes, diagnoses, tests and a prescription to it."
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="record-visit" loading={formState.isSubmitting}>
            Start visit
          </Button>
        </>
      }
    >
      <form id="record-visit" onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
        {formError && <Alert tone="danger">{formError}</Alert>}
        <Field label="Type of visit" error={formState.errors.visit_type?.message}>
          {(p) => (
            <Select {...p} {...register("visit_type")}>
              <option value="in_person">In person</option>
              <option value="teleconsult">Teleconsultation</option>
              <option value="home_visit">Home visit</option>
              <option value="emergency">Emergency</option>
            </Select>
          )}
        </Field>
        <Field
          label="Reason for visit"
          hint="In the patient's words, as you would record it."
          error={formState.errors.chief_complaint?.message}
        >
          {(p) => <Textarea {...p} rows={3} {...register("chief_complaint")} />}
        </Field>
        <Field label="Location" error={formState.errors.location?.message}>
          {(p) => <Input {...p} placeholder="Clinic or room" {...register("location")} />}
        </Field>
      </form>
    </Dialog>
  );
}

// --- Document diagnosis ----------------------------------------------------------------------

const diagnosisSchema = z.object({
  name: z.string().trim().min(1, "Enter the diagnosis as you would document it").max(300),
  icd10_code: z
    .string()
    .trim()
    .max(10)
    .optional()
    .refine((v) => !v || /^[A-Za-z][0-9][0-9A-Za-z](\.[0-9A-Za-z]{1,4})?$/.test(v), "Use ICD-10 format, e.g. E11.9"),
  verification_status: z.enum(["provisional", "confirmed", "unconfirmed"]),
  clinical_status: z.enum(["active", "recurrence", "relapse", "inactive", "remission", "resolved"]),
  severity: z.enum(["", "mild", "moderate", "severe"]),
  onset_date: z.string().optional().refine((v) => !v || v <= todayIso(), "Onset cannot be in the future"),
  notes: z.string().trim().max(2000).optional(),
});
type DiagnosisValues = z.infer<typeof diagnosisSchema>;

export function DiagnosisDialog({
  patientId,
  visitId,
  open,
  onClose,
}: {
  patientId: string;
  visitId?: string;
  open: boolean;
  onClose: () => void;
}) {
  const document = useDocumentCondition(patientId);
  const { register, handleSubmit, reset, setError, formState } = useForm<DiagnosisValues>({
    resolver: zodResolver(diagnosisSchema),
    defaultValues: { verification_status: "provisional", clinical_status: "active", severity: "" },
  });
  const { run, formError, clearFormError } = useSubmit(setError);
  const close = () => {
    reset();
    clearFormError();
    onClose();
  };
  const onSubmit = handleSubmit(async (v) => {
    const ok = await run(
      () =>
        document.mutateAsync({
          name: v.name,
          icd10_code: v.icd10_code || null,
          verification_status: v.verification_status,
          clinical_status: v.clinical_status,
          severity: v.severity || null,
          onset_date: v.onset_date || null,
          notes: v.notes || null,
          visit_id: visitId ?? null,
        }),
      "Assessment recorded",
    );
    if (ok) close();
  });

  const e = formState.errors;
  return (
    <Dialog
      open={open}
      onClose={close}
      title="Record assessment / diagnosis"
      description="Recorded exactly as you document it, attributed to you."
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="diagnosis" loading={formState.isSubmitting}>
            Record
          </Button>
        </>
      }
    >
      <form id="diagnosis" onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
        {formError && (
          <div className="sm:col-span-2">
            <Alert tone="danger">{formError}</Alert>
          </div>
        )}
        <Field label="Assessment / diagnosis" required error={e.name?.message} className="sm:col-span-2">
          {(p) => <Input {...p} {...register("name")} />}
        </Field>
        <Field label="Certainty" error={e.verification_status?.message}>
          {(p) => (
            <Select {...p} {...register("verification_status")}>
              <option value="provisional">Provisional</option>
              <option value="confirmed">Confirmed</option>
              <option value="unconfirmed">Reported, not confirmed</option>
            </Select>
          )}
        </Field>
        <Field label="Status" error={e.clinical_status?.message}>
          {(p) => (
            <Select {...p} {...register("clinical_status")}>
              <option value="active">Active</option>
              <option value="recurrence">Recurrence</option>
              <option value="relapse">Relapse</option>
              <option value="remission">Remission</option>
              <option value="inactive">Inactive</option>
              <option value="resolved">Resolved</option>
            </Select>
          )}
        </Field>
        <Field label="ICD-10 code" hint="Optional" error={e.icd10_code?.message}>
          {(p) => <Input {...p} placeholder="e.g. E11.9" {...register("icd10_code")} />}
        </Field>
        <Field label="Severity" error={e.severity?.message}>
          {(p) => (
            <Select {...p} {...register("severity")}>
              <option value="">Not recorded</option>
              <option value="mild">Mild</option>
              <option value="moderate">Moderate</option>
              <option value="severe">Severe</option>
            </Select>
          )}
        </Field>
        <Field label="Onset date" error={e.onset_date?.message}>
          {(p) => <Input {...p} type="date" max={todayIso()} {...register("onset_date")} />}
        </Field>
        <Field label="Notes" error={e.notes?.message} className="sm:col-span-2">
          {(p) => <Textarea {...p} rows={3} {...register("notes")} />}
        </Field>
      </form>
    </Dialog>
  );
}
