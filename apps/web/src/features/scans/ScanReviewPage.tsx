/**
 * Review an AI reading (or type in) a paper prescription next to its photo.
 *
 * Rules shown in the UI (AI_SAFETY.md §4):
 * - Low-confidence fields are empty and say "Could not confidently read this field."
 *   The uncertain reading is only used if the person chooses it and then confirms it.
 * - Medicine, strength, dose and frequency always need an explicit decision.
 * - Nothing is saved until every flagged field is decided; the AI reading itself is
 *   never changed, and corrections are recorded next to it.
 */
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, CircleSlash, Loader2, Pencil, Plus, RotateCcw, Sparkles, Trash2, TriangleAlert, Undo2 } from "lucide-react";
import { useState, type CSSProperties } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Badge, Button, Card, Dialog, ErrorState, Field, Input, SkeletonList, Textarea, cn, useToast } from "@/components/ui";
import { documentDownloadUrl, keys } from "@/features/chart/api";
import { useActivePatient } from "@/features/patient/context";
import { errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

import { useConfirmScan, useRejectScan, useReviewScan, useScan, useStartScan, type ReviewOp, type Scan, type ScanField } from "./api";

const LABELS: Record<string, string> = {
  medicine_name: "Medicine",
  strength: "Strength",
  dose: "Dose (amount each time)",
  frequency: "How often",
  duration: "For how long",
  meal_relation: "With food",
  instructions: "Other instructions",
  doctor_name: "Doctor",
  doctor_registration: "Doctor's registration number",
  clinic_name: "Clinic or hospital",
  prescription_date: "Date on the prescription",
};
const ITEM_FIELDS = ["medicine_name", "strength", "dose", "frequency", "duration", "meal_relation", "instructions"] as const;
const HEADER_FIELDS = ["doctor_name", "doctor_registration", "clinic_name", "prescription_date"] as const;

type Region = { x: number; y: number; w: number; h: number };

function padded(r: Region): Region {
  const px = 0.02;
  const x = Math.max(0, r.x - px);
  const y = Math.max(0, r.y - px);
  return { x, y, w: Math.min(1 - x, r.w + 2 * px), h: Math.min(1 - y, r.h + 2 * px) };
}

/** The part of the photo a field was read from, shown next to the field. */
export function RegionCrop({ src, region }: { src: string; region: Region }) {
  const r = padded(region);
  const style: CSSProperties = {
    backgroundImage: `url("${src}")`,
    backgroundSize: `${100 / r.w}% ${100 / r.h}%`,
    backgroundPosition: `${r.w >= 1 ? 0 : (r.x / (1 - r.w)) * 100}% ${r.h >= 1 ? 0 : (r.y / (1 - r.h)) * 100}%`,
    aspectRatio: `${Math.max(r.w / r.h, 0.5)}`,
  };
  return <div role="img" aria-label="Where this was read on the photo" className="h-10 max-w-48 rounded border border-line bg-no-repeat" style={style} />;
}

function StatusChip({ field }: { field: ScanField }) {
  const s = field.review.status;
  if (s === "confirmed") return <Badge tone="success"><Check className="mr-1 inline size-3" aria-hidden />Confirmed</Badge>;
  if (s === "corrected") return <Badge tone="success"><Pencil className="mr-1 inline size-3" aria-hidden />Corrected</Badge>;
  if (s === "not_on_prescription") return <Badge tone="neutral">Not on the prescription</Badge>;
  const ai = field.ai;
  if (!ai) return <Badge tone="neutral">To fill in</Badge>;
  if (ai.band === "low") return <Badge tone="danger">Unclear</Badge>;
  if (ai.band === "medium") return <Badge tone="warning">Check this</Badge>;
  if (ai.band === "absent") return <Badge tone="neutral">Not found on the photo</Badge>;
  return <Badge tone={field.requires_confirmation ? "info" : "neutral"}>{field.requires_confirmation ? "Read clearly · please confirm" : "Read clearly"}</Badge>;
}

export function FieldRow({
  label,
  field,
  itemKey,
  name,
  editable,
  photo,
  busy,
  onOps,
  onFocus,
}: {
  label: string;
  field: ScanField;
  itemKey: string | null;
  name: string;
  editable: boolean;
  photo: string | null;
  busy: boolean;
  onOps: (ops: ReviewOp[]) => Promise<void>;
  onFocus: (r: Region | null) => void;
}) {
  const [draft, setDraft] = useState(field.review.value ?? "");
  const [reveal, setReveal] = useState(false);
  const ai = field.ai;
  const low = ai?.band === "low";
  const status = field.review.status;
  const decided = status !== "unverified";
  const op = (action: ReviewOp["action"], value?: string): ReviewOp => ({ op: "set_field", item_key: itemKey, field: name, action, value: value ?? null });
  const region = (ai?.region ?? null) as Region | null;
  const inputId = `f-${itemKey ?? "h"}-${name}`;
  const needsAttention = !decided && (field.requires_confirmation || (ai !== null && ai.band !== "high" && ai.band !== "absent"));

  return (
    <div
      className={cn("rounded-lg border p-3", needsAttention ? (low ? "border-danger/60" : "border-warning/60") : "border-line")}
      onFocus={() => onFocus(region)}
      onMouseEnter={() => onFocus(region)}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label htmlFor={inputId} className="text-sm font-medium">
          {label}
          {field.critical && <span className="font-normal text-muted"> · must be checked</span>}
        </label>
        <StatusChip field={field} />
      </div>

      {low && !decided && (
        <div className="mt-2">
          <Alert tone="warning">
            <span className="flex items-start gap-2">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden /> Could not confidently read this field.
            </span>
            {ai?.value && editable && (
              <span className="mt-1 block">
                {reveal ? (
                  <>
                    Uncertain reading: <strong>“{ai.value}”</strong>. Only use it if the photo clearly says this.{" "}
                    <button type="button" className="font-semibold underline" onClick={() => setDraft(ai.value ?? "")}>
                      Put it in the box
                    </button>
                  </>
                ) : (
                  <button type="button" className="font-semibold underline" onClick={() => setReveal(true)}>
                    Show what the AI thought it said
                  </button>
                )}
              </span>
            )}
          </Alert>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-3">
        {name === "instructions" ? (
          <Textarea id={inputId} rows={2} className="min-w-56 flex-1" value={draft} disabled={!editable || status === "not_on_prescription"} onChange={(e) => setDraft(e.target.value)} />
        ) : (
          <Input
            id={inputId}
            className="min-w-40 flex-1"
            value={draft}
            placeholder={status === "not_on_prescription" ? "Not on the prescription" : "Type what the prescription says"}
            disabled={!editable || status === "not_on_prescription"}
            onChange={(e) => setDraft(e.target.value)}
          />
        )}
        {photo && region && <RegionCrop src={photo} region={region} />}
      </div>

      <div className="mt-1 flex flex-col gap-0.5 text-xs text-muted">
        {ai?.evidence && ai.evidence !== ai.value && (!low || reveal) && <span>Written as: “{ai.evidence}”</span>}
        {field.interpretation && <span>Means: {field.interpretation}</span>}
        {ai && ai.band !== "absent" && (
          <span>
            AI confidence {Math.round(ai.confidence * 100)}%
            {ai.flags.includes("handwritten") && " · handwritten"}
            {ai.flags.includes("ocr_disagrees") && " · the two readings disagree"}
            {ai.flags.includes("value_not_in_evidence") && " · not supported by the visible text"}
          </span>
        )}
      </div>

      {editable && (
        <div className="mt-2 flex flex-wrap gap-2">
          {status !== "not_on_prescription" && (
            <Button
              size="sm"
              disabled={busy || !draft.trim() || (decided && draft.trim() === (field.review.value ?? ""))}
              icon={<Check className="size-4" />}
              onClick={() => void onOps([draft.trim() === (field.review.value ?? "") ? op("confirm") : op("set", draft.trim())])}
            >
              {draft.trim() && draft.trim() !== (field.review.value ?? "") ? "Save" : "Confirm"}
            </Button>
          )}
          {name !== "medicine_name" && status !== "not_on_prescription" && (
            <Button size="sm" variant="secondary" disabled={busy} icon={<CircleSlash className="size-4" />} onClick={() => void onOps([op("not_on_prescription")]).then(() => setDraft(""))}>
              Not on the prescription
            </Button>
          )}
          {decided && (
            <Button size="sm" variant="ghost" disabled={busy} icon={<Undo2 className="size-4" />} onClick={() => void onOps([op("reset")]).then(() => setDraft(""))}>
              Undo
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function Photo({ src, focus }: { src: string | null; focus: Region | null }) {
  if (!src) return <SkeletonList rows={1} />;
  return (
    <div className="relative overflow-hidden rounded-xl border border-line bg-surface-2">
      <img src={src} alt="Uploaded prescription" className="block w-full" />
      {focus && (
        <div
          aria-hidden
          className="pointer-events-none absolute rounded border-2 border-accent bg-accent/10"
          style={{ left: `${focus.x * 100}%`, top: `${focus.y * 100}%`, width: `${focus.w * 100}%`, height: `${focus.h * 100}%` }}
        />
      )}
    </div>
  );
}

function StatusPanel({ scan }: { scan: Scan }) {
  const { patientId, base, can } = useActivePatient();
  const start = useStartScan(patientId);
  const navigate = useNavigate();
  const toast = useToast();
  const restart = async (mode: "ai" | "manual") => {
    try {
      const next = await start.mutateAsync({ document_id: scan.document_id, mode });
      void navigate(`${base}/prescriptions/scan/${next.id}`, { replace: true });
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };
  if (scan.status === "queued" || scan.status === "running") {
    return (
      <Card>
        <p className="flex items-center gap-2"><Loader2 className="size-5 animate-spin" aria-hidden /> Reading the prescription… this usually takes under a minute.</p>
      </Card>
    );
  }
  if (scan.status === "failed") {
    return (
      <Alert tone="danger" title="The prescription could not be read">
        <p>{scan.error_message}</p>
        {can("upload_reports") && (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => void restart("manual")} loading={start.isPending}>Type it in instead</Button>
            {scan.error_code !== "unsupported_type" && scan.error_code !== "too_small" && (
              <Button size="sm" variant="secondary" icon={<RotateCcw className="size-4" />} onClick={() => void restart("ai")}>Try again</Button>
            )}
          </div>
        )}
      </Alert>
    );
  }
  if (scan.status === "verified") {
    return (
      <Alert tone="success" title="Saved to the prescriptions">
        Checked {scan.verified_at ? formatDateTime(scan.verified_at) : ""} ({scan.verification_level === "doctor_verified" ? "doctor-verified" : "patient-verified"}).{" "}
        {scan.prescription_id && <Link className="font-semibold underline" to={`${base}/prescriptions/${scan.prescription_id}`}>Open the prescription</Link>}
      </Alert>
    );
  }
  if (scan.status === "rejected") {
    return <Alert tone="info" title="Discarded">Nothing from this photo was saved. Reason: {scan.rejected_reason}</Alert>;
  }
  return null;
}

export function ScanReviewPage() {
  const { patientId, base, mode, can } = useActivePatient();
  const { scanId = "" } = useParams();
  const scan = useScan(patientId, scanId);
  const review = useReviewScan(patientId, scanId);
  const confirm = useConfirmScan(patientId, scanId);
  const reject = useRejectScan(patientId, scanId);
  const toast = useToast();
  const navigate = useNavigate();
  const [focus, setFocus] = useState<Region | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const [reason, setReason] = useState("");

  const documentId = scan.data?.document_id;
  const photo = useQuery({
    queryKey: [...keys.section(patientId, "documents"), documentId, "url"],
    enabled: !!documentId,
    staleTime: 45_000,
    queryFn: () => documentDownloadUrl(patientId, documentId!),
  });

  const canVerify =
    can("upload_reports") &&
    (mode === "self" || (mode === "doctor" ? can("edit_clinical_records") : can("report_health_info")));

  const run = async (ops: ReviewOp[]) => {
    try {
      await review.mutateAsync(ops);
    } catch (err) {
      toast.error(errorMessage(err));
      throw err;
    }
  };
  const save = async () => {
    try {
      const done = await confirm.mutateAsync();
      toast.success("Prescription saved");
      void navigate(`${base}/prescriptions/${done.prescription_id}`);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <>
      <PageHeader
        back={<Link to={`${base}/prescriptions`} className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-fg"><ArrowLeft className="size-4" aria-hidden /> Prescriptions</Link>}
        title="Check the prescription"
        description="Compare every field with the photo. Nothing is saved until you press Save."
      />
      {scan.isPending ? (
        <SkeletonList rows={4} />
      ) : scan.isError ? (
        <ErrorState message={errorMessage(scan.error)} onRetry={() => void scan.refetch()} />
      ) : (
        <ReviewBody
          scan={scan.data}
          photo={photo.data ?? null}
          focus={focus}
          setFocus={setFocus}
          editable={canVerify && scan.data.status === "needs_review"}
          busy={review.isPending}
          onOps={run}
          onSave={() => void save()}
          saving={confirm.isPending}
          onDiscard={() => setDiscarding(true)}
        />
      )}
      <Dialog
        open={discarding}
        onClose={() => setDiscarding(false)}
        title="Discard this reading?"
        description="Nothing from this photo will be saved. The photo stays in your documents."
        footer={
          <>
            <Button variant="secondary" onClick={() => setDiscarding(false)}>Back</Button>
            <Button
              variant="danger"
              loading={reject.isPending}
              disabled={reason.trim().length < 3}
              onClick={() =>
                void reject.mutateAsync(reason.trim()).then(
                  () => {
                    setDiscarding(false);
                    toast.success("Discarded");
                  },
                  (err) => toast.error(errorMessage(err)),
                )
              }
            >
              Discard
            </Button>
          </>
        }
      >
        <Field label="Why?" hint="For example: wrong photo, blurred, not a prescription">
          {(p) => <Input {...p} value={reason} onChange={(e) => setReason(e.target.value)} />}
        </Field>
      </Dialog>
    </>
  );
}

export function ReviewBody({
  scan,
  photo,
  focus,
  setFocus,
  editable,
  busy,
  onOps,
  onSave,
  saving,
  onDiscard,
}: {
  scan: Scan;
  photo: string | null;
  focus: Region | null;
  setFocus: (r: Region | null) => void;
  editable: boolean;
  busy: boolean;
  onOps: (ops: ReviewOp[]) => Promise<void>;
  onSave: () => void;
  saving: boolean;
  onDiscard: () => void;
}) {
  const live = scan.items.filter((i) => !i.removed);
  const removed = scan.items.filter((i) => i.removed);
  const reviewing = scan.status === "needs_review";
  const fieldKey = (f: ScanField) => `${f.review.status}|${f.review.value ?? ""}`;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div className="lg:sticky lg:top-4 lg:self-start">
        <Photo src={photo} focus={focus} />
        <p className="mt-2 text-xs text-muted">Hover or tab to a field to see where it was read on the photo.</p>
      </div>

      <div className="flex flex-col gap-4">
        <StatusPanel scan={scan} />
        {reviewing && (
          <>
            {scan.mode === "ai" ? (
              <Alert tone="info">
                <span className="flex items-start gap-2">
                  <Sparkles className="mt-0.5 size-4 shrink-0" aria-hidden />
                  Read by AI. It can make mistakes and does not give medical advice. Check each field against the photo; missing
                  values are never filled in for you.
                </span>
              </Alert>
            ) : (
              <Alert tone="info">Type in what the prescription says. Leave out anything that is not written on it.</Alert>
            )}
            {scan.handwritten && <Alert tone="warning" title="Handwritten prescription">Handwriting is harder to read. Check every field carefully.</Alert>}
            {scan.is_prescription === false && <Alert tone="warning" title="This may not be a prescription">The AI did not recognise this photo as a prescription.</Alert>}
            {scan.reading_notes.length > 0 && (
              <Alert tone="warning" title="Notes from the reading">
                <ul className="list-inside list-disc">{scan.reading_notes.map((n) => <li key={n}>{n}</li>)}</ul>
              </Alert>
            )}
            {scan.discarded_keys.length > 0 && (
              <p className="text-xs text-muted">The AI also returned information this app does not collect (such as a diagnosis). It was discarded.</p>
            )}
          </>
        )}

        {(reviewing || scan.status === "verified") && (
          <>
            <Card title="Prescription details">
              <div className="grid gap-3">
                {HEADER_FIELDS.map((f) => (
                  <FieldRow key={`${f}-${fieldKey(scan.header[f]!)}`} label={LABELS[f]!} field={scan.header[f]!} itemKey={null} name={f} editable={editable} photo={photo} busy={busy} onOps={onOps} onFocus={setFocus} />
                ))}
              </div>
            </Card>

            {live.map((item, n) => (
              <Card
                key={item.key}
                title={
                  <span className="flex items-center gap-2">
                    Medicine {n + 1} {item.from_ai ? <Badge tone="info">Read by AI</Badge> : <Badge tone="neutral">Typed in</Badge>}
                  </span>
                }
                action={
                  editable && (
                    <Button size="sm" variant="ghost" icon={<Trash2 className="size-4" />} onClick={() => void onOps([{ op: "remove_item", item_key: item.key }])}>
                      Remove line
                    </Button>
                  )
                }
              >
                <div className="grid gap-3">
                  {ITEM_FIELDS.map((f) => (
                    <FieldRow key={`${f}-${fieldKey(item.fields[f]!)}`} label={LABELS[f]!} field={item.fields[f]!} itemKey={item.key} name={f} editable={editable} photo={photo} busy={busy} onOps={onOps} onFocus={setFocus} />
                  ))}
                </div>
              </Card>
            ))}

            {editable && (
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" icon={<Plus className="size-4" />} onClick={() => void onOps([{ op: "add_item" }])}>
                  Add a medicine line
                </Button>
                {removed.map((item) => (
                  <Button key={item.key} variant="ghost" onClick={() => void onOps([{ op: "restore_item", item_key: item.key }])}>
                    Restore removed line “{item.fields.medicine_name?.ai?.value ?? item.fields.medicine_name?.review.value ?? item.key}”
                  </Button>
                ))}
              </div>
            )}

            {editable && (
              <div className="sticky bottom-0 z-10 -mx-4 border-t border-line bg-surface/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-xl sm:border">
                {scan.issues.length > 0 ? (
                  <p className="mb-2 text-sm">
                    <strong>{scan.issues.length}</strong> field{scan.issues.length === 1 ? "" : "s"} still need{scan.issues.length === 1 ? "s" : ""} your check.
                  </p>
                ) : (
                  <p className="mb-2 text-sm text-success">Everything has been checked.</p>
                )}
                <div className="flex flex-wrap gap-2">
                  <Button onClick={onSave} disabled={scan.issues.length > 0} loading={saving}>Save prescription</Button>
                  <Button variant="ghost" onClick={onDiscard}>Discard</Button>
                </div>
                <p className="mt-2 text-xs text-muted">
                  Saved as a prescription you checked (not issued by a doctor on Health Io). Its medicines then wait for you to set reminder times.
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
