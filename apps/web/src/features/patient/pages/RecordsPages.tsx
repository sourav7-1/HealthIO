/** Read-only views of what doctors recorded, plus the patient's own additions. */
import { ArrowLeft, Camera, Download, FileText, Lock, Plus, ScanLine, Stethoscope, Upload } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Badge, Button, Card, Dialog, EmptyState, Field, Input, Select, Textarea, useToast } from "@/components/ui";
import {
  documentDownloadUrl,
  uploadDocument,
  useAppointments,
  useDocuments,
  useFollowUps,
  useMedicalHistory,
  usePrescriptions,
  useReports,
  useTestOrders,
  useVisit,
  useVisits,
} from "@/features/chart/api";
import { PrescriptionView } from "@/features/chart/PrescriptionView";
import { useScans } from "@/features/scans/api";
import { AddPrescriptionPhoto } from "@/features/scans/AddPrescriptionPhoto";
import { sourceLabel } from "@/features/reports/api";
import { PatientUploadReportDialog } from "@/features/reports/PatientUploadReportDialog";
import { ReportDetailDialog } from "@/features/reports/ReportDetailDialog";
import { QueryState, StatusBadge } from "@/features/chart/shared";
import { errorMessage, type Schemas } from "@/lib/api";
import { bytes, formatDate, formatDateTime, humanize, isInPast, todayIso } from "@/lib/format";

import { useRemoveSelfReported, useReportAllergy, useReportCondition } from "../api";
import { SourceBadge } from "../components";
import { useActivePatient, usePatientId } from "../context";
import { useQueryClient } from "@tanstack/react-query";
import { keys } from "@/features/chart/api";

const READ_ONLY_NOTE = "Records written by a doctor cannot be changed here. If something looks wrong, ask the doctor to correct it.";

// --- Medical history -------------------------------------------------------------------------

export function HistoryPage() {
  const { patientId: pid, can } = useActivePatient();
  const canReport = can("report_health_info");
  const history = useMedicalHistory(pid);
  const remove = useRemoveSelfReported(pid);
  const toast = useToast();
  const [adding, setAdding] = useState<"allergy" | "condition" | null>(null);

  const removeEntry = async (kind: "allergies" | "conditions", id: string) => {
    try {
      await remove.mutateAsync({ kind, entry_id: id });
      toast.success("Removed");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <>
      <PageHeader
        title="Medical history"
        description={READ_ONLY_NOTE}
        actions={
          canReport && (
            <>
              <Button variant="secondary" icon={<Plus className="size-4" />} onClick={() => setAdding("allergy")}>Add an allergy</Button>
              <Button variant="secondary" icon={<Plus className="size-4" />} onClick={() => setAdding("condition")}>Add a condition</Button>
            </>
          )
        }
      />
      <QueryState query={history} what="Medical history">
        {(h) => (
          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Allergies">
              {h.allergies.length === 0 ? (
                <p className="text-sm text-muted">No allergies recorded. If you have any, add them so your doctors know.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {h.allergies.map((a) => (
                    <li key={a.id} className="flex flex-wrap items-start justify-between gap-2 py-3 first:pt-0 last:pb-0">
                      <div>
                        <p className="font-medium">{a.substance}</p>
                        <p className="text-sm text-muted">
                          {a.reaction ?? "Reaction not recorded"}
                          {a.severity ? ` · ${humanize(a.severity)}` : ""}
                        </p>
                        {a.source !== "doctor" && a.verification_status === "unconfirmed" && (
                          <p className="text-xs text-muted">Not yet confirmed by a doctor</p>
                        )}
                      </div>
                      <div className="flex flex-col items-end gap-2">
                        <SourceBadge source={a.source} />
                        {canReport && (a.source === "patient" || a.source === "caregiver") && (
                          <Button size="sm" variant="ghost" onClick={() => void removeEntry("allergies", a.id)}>Remove</Button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card title="Conditions">
              {h.conditions.length === 0 ? (
                <p className="text-sm text-muted">No conditions recorded.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {h.conditions.map((c) => (
                    <li key={c.id} className="flex flex-wrap items-start justify-between gap-2 py-3 first:pt-0 last:pb-0">
                      <div>
                        <p className="font-medium">{c.name}</p>
                        <p className="text-sm text-muted">
                          {humanize(c.clinical_status)}
                          {c.onset_date ? ` · since ${formatDate(c.onset_date)}` : ""}
                        </p>
                        {c.source === "doctor" && (
                          <p className="text-xs text-muted">As recorded by your doctor ({humanize(c.verification_status).toLowerCase()})</p>
                        )}
                      </div>
                      <div className="flex flex-col items-end gap-2">
                        <SourceBadge source={c.source} />
                        {c.source === "patient" || c.source === "caregiver" ? (
                          canReport && <Button size="sm" variant="ghost" onClick={() => void removeEntry("conditions", c.id)}>Remove</Button>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs text-muted"><Lock className="size-3" aria-hidden /> Read only</span>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            {h.history.length > 0 && (
              <Card title="Past history" className="lg:col-span-2">
                <ul className="divide-y divide-line">
                  {h.history.map((e) => (
                    <li key={e.id} className="flex justify-between gap-2 py-3 text-sm first:pt-0 last:pb-0">
                      <span><span className="font-medium">{e.title}</span> · {humanize(e.category)}</span>
                      <span className="text-muted">{formatDate(e.occurred_on)}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        )}
      </QueryState>
      {adding === "allergy" && <AllergyDialog patientId={pid} onClose={() => setAdding(null)} />}
      {adding === "condition" && <ConditionDialog patientId={pid} onClose={() => setAdding(null)} />}
    </>
  );
}

function AllergyDialog({ patientId, onClose }: { patientId: string; onClose: () => void }) {
  const report = useReportAllergy(patientId);
  const toast = useToast();
  const [substance, setSubstance] = useState("");
  const [category, setCategory] = useState<Schemas["AllergenCategory"]>("medication");
  const [reaction, setReaction] = useState("");
  const [severity, setSeverity] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!substance.trim()) return setError("Enter what you are allergic to.");
    try {
      await report.mutateAsync({
        substance: substance.trim(),
        category,
        reaction: reaction.trim() || null,
        severity: (severity || null) as Schemas["ReactionSeverity"] | null,
      });
      toast.success("Allergy added");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add an allergy"
      description="Shown to your doctors as reported by you."
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => void save()} loading={report.isPending}>Add</Button></>}
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <Field label="Allergic to" required>{(p) => <Input {...p} value={substance} onChange={(e) => setSubstance(e.target.value)} />}</Field>
        <Field label="Type">
          {(p) => (
            <Select {...p} value={category} onChange={(e) => setCategory(e.target.value as Schemas["AllergenCategory"])}>
              <option value="medication">A medicine</option>
              <option value="food">A food</option>
              <option value="environment">Something in the environment (pollen, dust…)</option>
              <option value="other">Something else</option>
            </Select>
          )}
        </Field>
        <Field label="What happens (reaction)">{(p) => <Input {...p} placeholder="e.g. rash, swelling" value={reaction} onChange={(e) => setReaction(e.target.value)} />}</Field>
        <Field label="How bad">
          {(p) => (
            <Select {...p} value={severity} onChange={(e) => setSeverity(e.target.value)}>
              <option value="">Not sure</option>
              <option value="mild">Mild</option>
              <option value="moderate">Moderate</option>
              <option value="severe">Severe</option>
              <option value="life_threatening">Life-threatening</option>
            </Select>
          )}
        </Field>
      </div>
    </Dialog>
  );
}

function ConditionDialog({ patientId, onClose }: { patientId: string; onClose: () => void }) {
  const report = useReportCondition(patientId);
  const toast = useToast();
  const [name, setName] = useState("");
  const [since, setSince] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!name.trim()) return setError("Enter the condition.");
    try {
      await report.mutateAsync({ name: name.trim(), onset_date: since || null, notes: notes.trim() || null });
      toast.success("Condition added");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add a condition you have"
      description="For example a condition diagnosed elsewhere. Shown to your doctors as reported by you."
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => void save()} loading={report.isPending}>Add</Button></>}
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <Field label="Condition" required>{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Since">{(p) => <Input {...p} type="date" max={todayIso()} value={since} onChange={(e) => setSince(e.target.value)} />}</Field>
        <Field label="Notes">{(p) => <Textarea {...p} rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
      </div>
    </Dialog>
  );
}

// --- Doctor visits -------------------------------------------------------------------------------

export function VisitsPage() {
  const { patientId: pid, base } = useActivePatient();
  const visits = useVisits(pid);
  return (
    <>
      <PageHeader title="Doctor visits" description="Visits and notes recorded by your doctors." />
      <Card bodyClassName="p-0">
        <QueryState
          query={visits}
          what="Visits"
          isEmpty={(v) => v.length === 0}
          empty={<EmptyState icon={<Stethoscope className="size-5" />} title="No visits yet" description="When a doctor records a visit with you, it appears here." />}
        >
          {(list) => (
            <ul className="divide-y divide-line">
              {list.map((v) => (
                <li key={v.id}>
                  <Link to={`${base}/visits/${v.id}`} className="flex min-h-14 flex-wrap items-center justify-between gap-2 px-4 py-3 hover:bg-surface-2">
                    <div>
                      <p className="font-medium">{formatDateTime(v.started_at)}</p>
                      <p className="text-sm text-muted">{v.doctor_name ?? "Doctor"} · {humanize(v.visit_type)}</p>
                    </div>
                    <StatusBadge status={v.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </QueryState>
      </Card>
    </>
  );
}

export function VisitDetailPage() {
  const { patientId: pid, base } = useActivePatient();
  const { visitId = "" } = useParams();
  const visit = useVisit(pid, visitId);
  return (
    <>
      <PageHeader
        back={<Link to={`${base}/visits`} className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-fg"><ArrowLeft className="size-4" aria-hidden /> Doctor visits</Link>}
        title="Visit"
        description={READ_ONLY_NOTE}
      />
      <QueryState query={visit} what="Visit">
        {({ visit: v, notes }) => (
          <div className="flex flex-col gap-6">
            <Card>
              <p className="font-medium">{formatDateTime(v.started_at)} · {v.doctor_name ?? "Doctor"}</p>
              {v.chief_complaint && <p className="mt-2 text-sm"><span className="text-muted">Reason for visit: </span>{v.chief_complaint}</p>}
            </Card>
            <Card title="Notes from your doctor">
              {notes.length === 0 ? (
                <p className="text-sm text-muted">No signed notes for this visit.</p>
              ) : (
                <div className="flex flex-col gap-4">
                  {notes.map((n) => (
                    <article key={n.id} className="rounded-xl border border-line p-4">
                      <p className="text-sm text-muted">
                        {n.note_type === "soap" ? "SOAP" : humanize(n.note_type)} note · {formatDateTime(n.signed_at)}
                        {n.status === "superseded" && " · replaced by a later correction"}
                      </p>
                      <p className="mt-2 whitespace-pre-line leading-relaxed">{n.body}</p>
                    </article>
                  ))}
                </div>
              )}
            </Card>
          </div>
        )}
      </QueryState>
    </>
  );
}

// --- Prescriptions ------------------------------------------------------------------------------

function PendingScans() {
  const { patientId, base, can } = useActivePatient();
  const scans = useScans(patientId, can("view_reports"));
  const open = (scans.data ?? []).filter((s) => ["queued", "running", "needs_review", "failed"].includes(s.status));
  if (open.length === 0) return null;
  return (
    <Card title="Paper prescriptions to check" className="mb-6">
      <ul className="divide-y divide-line">
        {open.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-3 first:pt-0 last:pb-0">
            <span className="flex items-center gap-2 text-sm">
              <ScanLine className="size-4 text-muted" aria-hidden />
              Photo added {formatDateTime(s.created_at)} ·{" "}
              {s.status === "needs_review" ? "waiting for your check" : s.status === "failed" ? "could not be read" : "being read…"}
            </span>
            <Link to={`${base}/prescriptions/scan/${s.id}`} className="inline-flex min-h-11 items-center rounded-lg border border-line px-4 text-sm font-semibold hover:bg-surface-2">
              {s.status === "needs_review" ? "Check it" : "Open"}
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function VerificationBadge({ status }: { status: string }) {
  return (
    <Badge tone="neutral">
      Paper prescription · {status === "doctor_verified" ? "checked by a doctor" : "checked by patient or family"}
    </Badge>
  );
}

export function PrescriptionsPage() {
  const { patientId: pid, base, mode, can } = useActivePatient();
  const rxs = usePrescriptions(pid);
  const self = mode === "self";
  const [adding, setAdding] = useState(false);
  return (
    <>
      <PageHeader
        title="Prescriptions"
        description={self ? "Prescriptions written for you by your doctors. Open one to see it in full or download it." : "Prescriptions written by their doctors. Open one to see it in full or download it."}
        actions={
          can("upload_reports") && (
            <Button icon={<Camera className="size-4" />} onClick={() => setAdding(true)}>
              Add a paper prescription
            </Button>
          )
        }
      />
      <PendingScans />
      {adding && <AddPrescriptionPhoto onClose={() => setAdding(false)} />}
      <QueryState
        query={rxs}
        what="Prescriptions"
        isEmpty={(r) => r.length === 0}
        empty={<Card><EmptyState icon={<FileText className="size-5" />} title="No prescriptions yet" /></Card>}
      >
        {(all) => {
          // A corrected prescription replaces its earlier version; history is on the detail page.
          const list = all.filter((rx) => rx.status !== "superseded");
          return (
            <div className="flex flex-col gap-4">
              {list.map((rx) => (
                <Card key={rx.id}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="font-semibold">{rx.prescriber_name ?? rx.external_prescriber_name ?? "Doctor"}</p>
                      <p className="text-sm text-muted">
                        {formatDate(rx.prescribed_on)}
                        {rx.valid_until ? ` · valid until ${formatDate(rx.valid_until)}` : ""}
                      </p>
                      {rx.revision > 1 && (
                        <p className="text-sm text-muted">Corrected by the doctor (version {rx.revision}){rx.revision_reason ? `: ${rx.revision_reason}` : ""}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      {rx.source === "uploaded" ? (
                        <VerificationBadge status={rx.verification_status} />
                      ) : (
                        <>
                          <SourceBadge source="prescription" />
                          <StatusBadge status={rx.status} />
                        </>
                      )}
                    </div>
                  </div>
                  {rx.diagnosis_as_written && <p className="mt-3 text-sm"><span className="text-muted">Diagnosis / assessment: </span>{rx.diagnosis_as_written}</p>}
                  <ol className="mt-3 flex flex-col gap-2">
                    {rx.items.map((i) => (
                      <li key={i.id} className="rounded-lg bg-surface-2 px-3 py-2">
                        <p className="font-medium">
                          {i.drug_name}
                          {i.strength ? ` ${i.strength}` : ""}
                          {i.generic_name && <span className="font-normal text-muted"> · {i.generic_name}</span>}
                        </p>
                        <p className="text-sm text-muted">
                          {[
                            i.dose_amount && `${Number(i.dose_amount)} ${i.dose_unit ?? ""}`.trim(),
                            i.frequency_text,
                            i.meal_relation && humanize(i.meal_relation),
                            i.duration_days && `for ${i.duration_days} days`,
                            i.is_prn && `when needed${i.prn_reason ? `: ${i.prn_reason}` : ""}`,
                            i.instructions,
                          ].filter(Boolean).join(" · ") || "No directions recorded"}
                        </p>
                      </li>
                    ))}
                  </ol>
                  {rx.advice && <p className="mt-3 text-sm"><span className="text-muted">Notes and advice: </span>{rx.advice}</p>}
                  {rx.follow_up_on && (
                    <p className="mt-2 text-sm">
                      <span className="text-muted">Follow-up: </span>on or before {formatDate(rx.follow_up_on)}
                      {rx.follow_up_instructions ? ` · ${rx.follow_up_instructions}` : ""}
                    </p>
                  )}
                  {rx.status === "cancelled" && <p className="mt-2 text-sm text-muted">Cancelled by the doctor{rx.cancel_reason ? `: ${rx.cancel_reason}` : ""}.</p>}
                  <div className="mt-4">
                    <Link to={`${base}/prescriptions/${rx.id}`} className="inline-flex min-h-11 items-center rounded-lg border border-line px-4 text-sm font-semibold hover:bg-surface-2">
                      View full prescription
                    </Link>
                  </div>
                </Card>
              ))}
            </div>
          );
        }}
      </QueryState>
    </>
  );
}

export function PrescriptionDetailPage() {
  const { patientId, base } = useActivePatient();
  const { prescriptionId = "" } = useParams();
  return (
    <>
      <PageHeader
        back={<Link to={`${base}/prescriptions`} className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-fg"><ArrowLeft className="size-4" aria-hidden /> Prescriptions</Link>}
        title="Prescription"
        description={READ_ONLY_NOTE}
      />
      <PrescriptionView patientId={patientId} prescriptionId={prescriptionId} hrefFor={(id) => `${base}/prescriptions/${id}`} />
    </>
  );
}

// --- Tests & reports ------------------------------------------------------------------------------

export function TestsPage() {
  const { patientId: pid, can, mode } = useActivePatient();
  const self = mode === "self";
  const orders = useTestOrders(pid);
  const reports = useReports(pid);
  const documents = useDocuments(pid);
  const toast = useToast();
  const [uploading, setUploading] = useState<"report" | "document" | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);
  const openOrders = (orders.data ?? []).filter((o) => ["ordered", "sample_collected", "partially_resulted"].includes(o.status));
  const reportDocs = new Set((reports.data ?? []).map((r) => r.document_id).filter(Boolean));

  const open = async (id: string) => {
    try {
      window.open(await documentDownloadUrl(pid, id), "_blank", "noopener,noreferrer");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <>
      <PageHeader
        title="Tests & reports"
        actions={
          can("upload_reports") && (
            <div className="flex flex-wrap gap-2">
              <Button icon={<Upload className="size-4" />} onClick={() => setUploading("report")}>Upload a report</Button>
              <Button variant="secondary" icon={<FileText className="size-4" />} onClick={() => setUploading("document")}>Other document</Button>
            </div>
          )
        }
      />
      <div className="flex flex-col gap-6">
        <Card title="Tests your doctor ordered" bodyClassName="p-0">
          <QueryState query={orders} what="Tests" isEmpty={(o) => o.length === 0} empty={<p className="p-4 text-sm text-muted">No tests ordered.</p>}>
            {(list) => (
              <ul className="divide-y divide-line">
                {list.map((o) => (
                  <li key={o.id} className="flex flex-wrap items-start justify-between gap-2 px-4 py-3">
                    <div className="min-w-0">
                      <p className="font-medium">{o.tests.join(", ")}</p>
                      <p className="text-sm text-muted">
                        Ordered {formatDate(o.ordered_at.slice(0, 10))} by {o.ordering_doctor_name ?? "your doctor"}
                        {o.due_by ? ` · needed by ${formatDate(o.due_by)}` : ""}
                      </p>
                      {o.clinical_indication && (
                        <p className="text-sm">
                          <span className="text-muted">Reason: </span>
                          {o.clinical_indication}
                        </p>
                      )}
                      {o.cancel_reason && <p className="text-sm text-muted">Cancelled: {o.cancel_reason}</p>}
                    </div>
                    <div className="flex items-center gap-2">
                      {o.report_ids.length > 0 && (
                        <Badge tone="info">
                          {o.report_ids.length} report{o.report_ids.length > 1 ? "s" : ""}
                        </Badge>
                      )}
                      <StatusBadge status={o.status} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
        </Card>
        <Card title="Reports" bodyClassName="p-0">
          <QueryState query={reports} what="Reports" isEmpty={(r) => r.length === 0} empty={<p className="p-4 text-sm text-muted">No reports yet.</p>}>
            {(list) => (
              <ul className="divide-y divide-line">
                {list.map((r) => (
                  <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                    <div className="min-w-0">
                      <p className="font-medium">{r.test_name ?? r.lab_name ?? "Report"}</p>
                      <p className="text-sm text-muted">
                        {formatDate(r.report_date ?? (r.collected_at ?? r.created_at).slice(0, 10))}
                        {r.lab_name && r.test_name ? ` · ${r.lab_name}` : ""}
                        {r.ordering_doctor_name ? ` · ordered by ${r.ordering_doctor_name}` : ""}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={r.source === "doctor" ? "info" : "neutral"}>{sourceLabel(r.source, self)}</Badge>
                      <StatusBadge status={r.status} label={r.status === "pending_review" ? "Awaiting review" : undefined} />
                      <Button size="sm" variant="secondary" onClick={() => setViewing(r.id)}>
                        View
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
          <p className="border-t border-line px-4 py-3 text-xs text-muted">
            Reports are shown exactly as they were added. Health Io never interprets results: ask your doctor what they mean for you.
          </p>
        </Card>
        <Card title="Other documents" bodyClassName="p-0">
          <QueryState
            query={documents}
            what="Documents"
            isEmpty={(d) => d.filter((x) => !reportDocs.has(x.id)).length === 0}
            empty={<p className="p-4 text-sm text-muted">No other documents yet. Add letters, discharge summaries or vaccination records so they are all in one place.</p>}
          >
            {(list) => (
              <ul className="divide-y divide-line">
                {list
                  .filter((d) => !reportDocs.has(d.id))
                  .map((d) => (
                    <li key={d.id} className="flex items-center justify-between gap-3 px-4 py-3">
                      <div className="min-w-0">
                        <p className="truncate font-medium">{d.title ?? humanize(d.document_type)}</p>
                        <p className="text-sm text-muted">
                          {formatDate(d.document_date ?? d.created_at.slice(0, 10))} · {bytes(d.size_bytes)}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <SourceBadge source={d.source} />
                        {d.scan_status === "clean" ? (
                          <Button size="sm" variant="secondary" icon={<Download className="size-4" />} onClick={() => void open(d.id)}>
                            Open
                          </Button>
                        ) : (
                          <Badge tone="warning">Checking</Badge>
                        )}
                      </div>
                    </li>
                  ))}
              </ul>
            )}
          </QueryState>
        </Card>
      </div>
      {uploading === "report" && <PatientUploadReportDialog patientId={pid} orders={openOrders} onClose={() => setUploading(null)} />}
      {uploading === "document" && <UploadDialog patientId={pid} onClose={() => setUploading(null)} />}
      {viewing && (
        <ReportDetailDialog
          patientId={pid}
          reportId={viewing}
          viewer="patient"
          self={self}
          canWithdraw={can("upload_reports")}
          onClose={() => setViewing(null)}
        />
      )}
    </>
  );
}

const ACCEPT = "application/pdf,image/jpeg,image/png,image/webp,image/heic";

function UploadDialog({ patientId, onClose }: { patientId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState<Schemas["DocumentType"]>("discharge_summary");
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pick = (f: File | null) => {
    setError(null);
    if (f && !ACCEPT.split(",").includes(f.type)) return setError("Upload a PDF or a photo (JPEG, PNG, WebP or HEIC).");
    if (f && f.size > 15 * 1024 * 1024) return setError("Files must be smaller than 15 MB.");
    setFile(f);
  };

  const save = async () => {
    if (!file) return setError("Choose a file to upload.");
    setBusy(true);
    try {
      await uploadDocument(patientId, file, { document_type: type, title: title.trim() || undefined, document_date: date || undefined });
      await qc.invalidateQueries({ queryKey: keys.patient(patientId) });
      toast.success("Document uploaded");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Upload a document"
      description="Stored privately and checked for viruses. A doctor sees it only if what you share with them covers this kind of document. For test reports, use Upload a report."
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => void save()} loading={busy}>Upload</Button></>}
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-line px-4 py-6 text-center hover:bg-surface-2">
          <Upload className="size-6 text-muted" aria-hidden />
          <span className="text-sm font-medium">{file ? file.name : "Choose a PDF or photo"}</span>
          <span className="text-xs text-muted">{file ? bytes(file.size) : "Up to 15 MB"}</span>
          <input type="file" accept={ACCEPT} className="sr-only" onChange={(e) => pick(e.target.files?.[0] ?? null)} />
        </label>
        <Field label="What is it?">
          {(p) => (
            <Select {...p} value={type} onChange={(e) => setType(e.target.value as Schemas["DocumentType"])}>
              <option value="prescription">Prescription from another doctor</option>
              <option value="discharge_summary">Hospital discharge summary</option>
              <option value="imaging_report">Scan or X-ray report</option>
              <option value="vaccination_record">Vaccination record</option>
              <option value="other">Something else</option>
            </Select>
          )}
        </Field>
        <Field label="Name (optional)">{(p) => <Input {...p} value={title} onChange={(e) => setTitle(e.target.value)} />}</Field>
        <Field label="Date on the document">{(p) => <Input {...p} type="date" max={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
      </div>
    </Dialog>
  );
}

// --- Appointments -----------------------------------------------------------------------------------

export function AppointmentsPage() {
  const pid = usePatientId();
  const appts = useAppointments(pid);
  const followUps = useFollowUps(pid);
  return (
    <>
      <PageHeader title="Appointments" />
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Appointments" className="lg:col-span-2" bodyClassName="p-0">
          <QueryState query={appts} what="Appointments" isEmpty={(a) => a.length === 0} empty={<p className="p-4 text-sm text-muted">No appointments. Your doctor can book one for you.</p>}>
            {(list) => (
              <ul className="divide-y divide-line">
                {[...list].sort((a, b) => b.starts_at.localeCompare(a.starts_at)).map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
                    <div>
                      <p className="font-medium">{formatDateTime(a.starts_at)} {!isInPast(a.starts_at) && <Badge tone="info">Upcoming</Badge>}</p>
                      <p className="text-sm text-muted">{a.doctor_name ?? "Your doctor"} · {humanize(a.mode)}{a.location ? ` · ${a.location}` : ""}</p>
                    </div>
                    <StatusBadge status={a.status} />
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
        </Card>
        <Card title="Follow-ups">
          <QueryState query={followUps} what="Follow-ups" isEmpty={(f) => f.length === 0} empty={<p className="text-sm text-muted">No follow-ups.</p>}>
            {(list) => (
              <ul className="flex flex-col gap-3">
                {list.map((f) => (
                  <li key={f.id} className="text-sm">
                    <p className="font-medium">By {formatDate(f.due_date)} <StatusBadge status={f.status} /></p>
                    <p className="text-muted">{f.doctor_name ?? "Your doctor"}{f.reason ? ` · ${f.reason}` : ""}</p>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
        </Card>
      </div>
    </>
  );
}
