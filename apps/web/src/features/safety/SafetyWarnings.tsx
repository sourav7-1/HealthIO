/**
 * Medication safety warnings for one person. Each is a *potential* issue to confirm with
 * a doctor or pharmacist: nothing here tells anyone to stop or change a medicine.
 * Doctors mark warnings reviewed (with a note); patients and caregivers acknowledge them.
 */
import { CheckCircle2, RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { Alert, Badge, Button, Card, Dialog, Field, Textarea, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { formatDate, formatDateTime, humanize } from "@/lib/format";

import {
  HEADLINE,
  usePrescriptionSafetyCheck,
  useRecheck,
  useReferenceDatasets,
  useReviewWarning,
  useSafetyWarnings,
  type SafetyFinding,
  type SafetyWarning,
} from "./api";

const TONE = { info: "info", caution: "warning", serious: "danger" } as const;
const SEVERITY_LABEL = { info: "For information", caution: "Check with doctor", serious: "Important: check soon" } as const;

function Coverage() {
  const datasets = useReferenceDatasets();
  if (!datasets.data) return null;
  return (
    <p className="text-xs text-muted">
      Checks for medicines listed twice, shared ingredients, allergies and prescription details.{" "}
      {datasets.data.length
        ? `Interaction and condition checks use: ${datasets.data.map((d) => `${d.name} (${d.version})`).join("; ")}.`
        : "No interaction database is loaded yet, so interactions and condition cautions are not checked."}
    </p>
  );
}

function WarningItem({ w, viewer, patientId }: { w: SafetyWarning; viewer: "doctor" | "patient"; patientId: string }) {
  const review = useReviewWarning(patientId);
  const toast = useToast();
  const [noting, setNoting] = useState(false);
  const [note, setNote] = useState("");
  const resolved = w.status === "resolved";

  const submit = async (withNote?: string) => {
    try {
      await review.mutateAsync({ warning_id: w.id, note: withNote });
      toast.success(viewer === "doctor" ? "Marked as reviewed" : "Thanks, noted");
      setNoting(false);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <li className={resolved ? "px-4 py-3 opacity-70" : "px-4 py-3"}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">{w.title}</p>
          <p className="mt-0.5 text-sm">{w.detail}</p>
          <p className="mt-1 text-xs text-muted">
            {humanize(w.kind)} · Source: {w.source_name}
            {w.source_version ? ` (${w.source_version})` : ""} · found {formatDate(w.detected_at.slice(0, 10))}
            {resolved && w.resolved_at ? ` · no longer applies since ${formatDate(w.resolved_at.slice(0, 10))}` : ""}
          </p>
          {w.review_status !== "unreviewed" && (
            <p className="mt-1 flex items-center gap-1 text-xs text-success">
              <CheckCircle2 className="size-3.5" aria-hidden />
              {w.review_status === "reviewed" ? "Reviewed by a doctor" : "Seen by the patient or a caregiver"}
              {w.reviewed_at ? ` · ${formatDateTime(w.reviewed_at)}` : ""}
              {w.review_note ? `: ${w.review_note}` : ""}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <Badge tone={TONE[w.severity]}>{SEVERITY_LABEL[w.severity]}</Badge>
          {!resolved && viewer === "patient" && w.review_status === "unreviewed" && (
            <Button size="sm" variant="secondary" loading={review.isPending} onClick={() => void submit()}>
              I've seen this
            </Button>
          )}
          {!resolved && viewer === "doctor" && w.review_status !== "reviewed" && (
            <Button size="sm" variant="secondary" onClick={() => setNoting(true)}>
              Mark reviewed
            </Button>
          )}
        </div>
      </div>
      {noting && (
        <div className="mt-3 flex flex-col gap-2 rounded-lg border border-line p-3">
          <Field label="Review note" hint="What you decided, e.g. aware and monitoring, or treatment updated.">
            {(p) => <Textarea {...p} rows={2} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />}
          </Field>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setNoting(false)}>
              Back
            </Button>
            <Button size="sm" disabled={note.trim().length < 3} loading={review.isPending} onClick={() => void submit(note.trim())}>
              Save review
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

export function SafetyWarningsCard({ patientId, viewer }: { patientId: string; viewer: "doctor" | "patient" }) {
  const [showResolved, setShowResolved] = useState(false);
  const warnings = useSafetyWarnings(patientId, showResolved);
  const recheck = useRecheck(patientId);
  const list = warnings.data ?? [];
  const open = list.filter((w) => w.status === "open");

  return (
    <Card
      title="Medication safety"
      bodyClassName="p-0"
      action={
        <Button size="sm" variant="ghost" icon={<RefreshCw className="size-4" />} loading={recheck.isPending} onClick={() => void recheck.mutateAsync()}>
          Check again
        </Button>
      }
    >
      <div className="flex flex-col gap-3 px-4 py-3">
        {open.length > 0 ? (
          <Alert tone="warning">
            <span className="flex items-start gap-2">
              <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>
                <strong>{HEADLINE}</strong>
                {viewer === "patient" && " Please don't stop, skip or change any medicine on your own because of this."}
              </span>
            </span>
          </Alert>
        ) : (
          warnings.isSuccess && (
            <p className="flex items-center gap-2 text-sm text-muted">
              <ShieldCheck className="size-4 text-success" aria-hidden /> No potential issues found by the available checks.
            </p>
          )
        )}
        <Coverage />
      </div>
      {list.length > 0 && (
        <ul className="divide-y divide-line border-t border-line">
          {list.map((w) => (
            <WarningItem key={w.id} w={w} viewer={viewer} patientId={patientId} />
          ))}
        </ul>
      )}
      <div className="border-t border-line px-4 py-2">
        <button type="button" className="text-sm text-accent hover:underline" onClick={() => setShowResolved((s) => !s)}>
          {showResolved ? "Hide past warnings" : "Show past warnings"}
        </button>
      </div>
    </Card>
  );
}

function FindingItem({ f }: { f: SafetyFinding }) {
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="font-medium">{f.title}</p>
        <Badge tone={TONE[f.severity]}>{SEVERITY_LABEL[f.severity]}</Badge>
      </div>
      <p className="text-sm">{f.detail}</p>
      <p className="text-xs text-muted">
        Source: {f.source_name}
        {f.source_version ? ` (${f.source_version})` : ""}
      </p>
    </li>
  );
}

/** Issue a prescription after seeing the safety check for it. */
export function IssueWithSafetyCheck({
  patientId,
  prescriptionId,
  description,
  open,
  onIssue,
  onClose,
}: {
  patientId: string;
  prescriptionId: string;
  description: string;
  open: boolean;
  onIssue: () => Promise<void>;
  onClose: () => void;
}) {
  const check = usePrescriptionSafetyCheck(patientId, prescriptionId, open);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const findings = check.data?.findings ?? [];
  const issue = async () => {
    setBusy(true);
    try {
      await onIssue();
      onClose();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title="Issue this prescription?"
      description={description}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Back to draft
          </Button>
          <Button onClick={() => void issue()} loading={busy} disabled={check.isPending}>
            {findings.length ? "Issue anyway" : "Issue prescription"}
          </Button>
        </>
      }
    >
      <section aria-label="Safety check" className="flex flex-col gap-3">
        {check.isPending ? (
          <p className="text-sm text-muted" role="status">
            Checking against current medicines, allergies and conditions…
          </p>
        ) : check.isError ? (
          <Alert tone="warning">The safety check could not run: {errorMessage(check.error)}</Alert>
        ) : findings.length ? (
          <>
            <Alert tone="warning">
              <strong>{HEADLINE}</strong> Review before issuing; the patient will see these as warnings to confirm with you.
            </Alert>
            <ul className="divide-y divide-line">
              {findings.map((f, i) => (
                <FindingItem key={i} f={f} />
              ))}
            </ul>
          </>
        ) : (
          <p className="flex items-center gap-2 text-sm">
            <ShieldCheck className="size-4 text-success" aria-hidden /> No potential issues found by the available checks.
          </p>
        )}
        {check.data && check.data.unknown_ingredients.length > 0 && (
          <p className="text-xs text-muted">
            Ingredients not known for: {check.data.unknown_ingredients.join(", ")} (add generic names to include them in ingredient
            checks).
          </p>
        )}
        {check.data && (
          <p className="text-xs text-muted">
            {check.data.datasets.length
              ? `Reference data: ${check.data.datasets.join("; ")}.`
              : "No interaction database is loaded, so interactions and condition cautions were not checked."}
          </p>
        )}
      </section>
    </Dialog>
  );
}
