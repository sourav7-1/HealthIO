import { useState } from "react";

import { Alert, Button, Dialog, Field, Input, Select, Textarea, useToast } from "@/components/ui";
import { useVisits } from "@/features/chart/api";
import { errorMessage, type Schemas } from "@/lib/api";
import { formatDate, humanize, todayIso } from "@/lib/format";

import { useCorrectSymptom, useDocumentSymptom, useReportSymptom, type Symptom } from "./api";

type Severity = Schemas["SymptomSeverity"];

interface Draft {
  symptom: string;
  body_site: string;
  severity: Severity | "";
  onset_date: string;
  resolved_on: string;
  notes: string;
}

const EMPTY: Draft = { symptom: "", body_site: "", severity: "", onset_date: "", resolved_on: "", notes: "" };

function body(d: Draft) {
  return {
    symptom: d.symptom.trim(),
    body_site: d.body_site.trim() || null,
    severity: d.severity || null,
    onset_date: d.onset_date || null,
    resolved_on: d.resolved_on || null,
    notes: d.notes.trim() || null,
  };
}

function SymptomFields({ draft, set }: { draft: Draft; set: (d: Draft) => void }) {
  const on = (key: keyof Draft) => (e: { target: { value: string } }) => set({ ...draft, [key]: e.target.value });
  return (
    <>
      <Field label="What is the symptom?" required hint="Describe it in your own words, for example “headache in the mornings”.">
        {(p) => <Input {...p} value={draft.symptom} onChange={on("symptom")} maxLength={300} />}
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Where (optional)">{(p) => <Input {...p} value={draft.body_site} onChange={on("body_site")} maxLength={100} />}</Field>
        <Field label="How bad (optional)">
          {(p) => (
            <Select {...p} value={draft.severity} onChange={on("severity")}>
              <option value="">Not stated</option>
              <option value="mild">Mild</option>
              <option value="moderate">Moderate</option>
              <option value="severe">Severe</option>
            </Select>
          )}
        </Field>
        <Field label="Started on">{(p) => <Input {...p} type="date" max={todayIso()} value={draft.onset_date} onChange={on("onset_date")} />}</Field>
        <Field label="Stopped on" hint="Leave empty if it is still going on.">
          {(p) => <Input {...p} type="date" max={todayIso()} min={draft.onset_date || undefined} value={draft.resolved_on} onChange={on("resolved_on")} />}
        </Field>
      </div>
      <Field label="Notes (optional)">{(p) => <Textarea {...p} rows={3} value={draft.notes} onChange={on("notes")} maxLength={2000} />}</Field>
    </>
  );
}

/** Patient/caregiver ("report") or doctor ("document", optionally at a visit). */
export function AddSymptomDialog({
  patientId,
  as,
  onClose,
}: {
  patientId: string;
  as: "report" | "document";
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [visitId, setVisitId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const report = useReportSymptom(patientId);
  const documented = useDocumentSymptom(patientId);
  const visits = useVisits(patientId, as === "document");
  const toast = useToast();
  const busy = report.isPending || documented.isPending;

  const save = async () => {
    if (!draft.symptom.trim()) return setError("Describe the symptom.");
    setError(null);
    try {
      if (as === "report") await report.mutateAsync(body(draft));
      else await documented.mutateAsync({ ...body(draft), visit_id: visitId || null });
      toast.success("Symptom added to the record");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={as === "report" ? "Report a symptom" : "Record a symptom"}
      description={
        as === "report"
          ? "Saved in your words and labelled as reported by you. It does not replace seeing a doctor. If it is severe or sudden, get medical help now."
          : "Recorded as the patient presented it, labelled with your name."
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} loading={busy}>Save</Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <SymptomFields draft={draft} set={setDraft} />
        {as === "document" && (
          <Field label="Visit (optional)">
            {(p) => (
              <Select {...p} value={visitId} onChange={(e) => setVisitId(e.target.value)}>
                <option value="">Not linked to a visit</option>
                {(visits.data ?? []).map((v) => (
                  <option key={v.id} value={v.id}>
                    {formatDate((v.started_at ?? "").slice(0, 10) || null)} · {humanize(v.visit_type)}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
      </div>
    </Dialog>
  );
}

/** A correction keeps the earlier version in the record's history and needs a reason. */
export function CorrectSymptomDialog({ patientId, symptom, onClose }: { patientId: string; symptom: Symptom; onClose: () => void }) {
  const [draft, setDraft] = useState<Draft>({
    symptom: symptom.symptom,
    body_site: symptom.body_site ?? "",
    severity: symptom.severity ?? "",
    onset_date: symptom.onset_date ?? "",
    resolved_on: symptom.resolved_on ?? "",
    notes: symptom.notes ?? "",
  });
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const correct = useCorrectSymptom(patientId);
  const toast = useToast();

  const save = async (markInError = false) => {
    if (reason.trim().length < 3) return setError("Say why you are changing this entry.");
    if (!markInError && !draft.symptom.trim()) return setError("Describe the symptom.");
    setError(null);
    try {
      await correct.mutateAsync(
        markInError
          ? { symptom_id: symptom.id, version: symptom.version, reason: reason.trim(), status: "entered_in_error" }
          : {
              symptom_id: symptom.id,
              version: symptom.version,
              reason: reason.trim(),
              ...body(draft),
              status: draft.resolved_on ? "resolved" : "ongoing",
            },
      );
      toast.success(markInError ? "Marked as entered by mistake" : "Correction saved");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Correct this symptom"
      description="The earlier version stays in the record's history with your reason."
      footer={
        <>
          <Button variant="ghost" onClick={() => void save(true)} disabled={correct.isPending}>Entered by mistake</Button>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} loading={correct.isPending}>Save correction</Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <SymptomFields draft={draft} set={setDraft} />
        <Field label="Reason for the change" required>
          {(p) => <Input {...p} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />}
        </Field>
      </div>
    </Dialog>
  );
}
