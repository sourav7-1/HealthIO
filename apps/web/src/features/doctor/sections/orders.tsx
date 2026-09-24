import { ClipboardPlus, Download, FileText, FlaskConical, Pill, TestTube } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { Alert, Badge, Button, Card, Dialog, EmptyState, Field, Textarea, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { bytes, formatDate, formatDateTime, humanize } from "@/lib/format";

import {
  documentDownloadUrl,
  useAdherence,
  useCancelOrder,
  useCancelPrescription,
  useDocuments,
  useIssuePrescription,
  useMedications,
  usePrescriptions,
  useReports,
  useTestOrders,
  type Overview,
  type Prescription,
} from "@/features/chart/api";
import { useScans } from "@/features/scans/api";
import { PrescriptionDialog, RecordMedicationDialog } from "../forms/prescription";
import { QueryState, SourceLabel, StatusBadge } from "@/features/chart/shared";

// --- Reason / confirm dialog -------------------------------------------------------------------

export function ReasonDialog({
  open,
  title,
  description,
  confirmLabel,
  danger,
  requireReason = true,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  danger?: boolean;
  requireReason?: boolean;
  onConfirm: (reason: string) => Promise<void>;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const close = () => {
    setReason("");
    setError(null);
    onClose();
  };
  const submit = async () => {
    if (requireReason && reason.trim().length < 3) {
      setError("Give a short reason (at least 3 characters).");
      return;
    }
    setBusy(true);
    try {
      await onConfirm(reason.trim());
      close();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onClose={close}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Back
          </Button>
          <Button variant={danger ? "danger" : "primary"} loading={busy} onClick={() => void submit()}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {requireReason ? (
        <Field label="Reason" required error={error ?? undefined}>
          {(p) => <Textarea {...p} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
        </Field>
      ) : (
        error && <Alert tone="danger">{error}</Alert>
      )}
    </Dialog>
  );
}

// --- Medications -------------------------------------------------------------------------------

export function MedicationsSection({ patientId, overview }: { patientId: string; overview: Overview }) {
  const meds = useMedications(patientId);
  const canAdherence = overview.permissions.includes("view_adherence");
  const adherence = useAdherence(patientId, canAdherence);
  const canRecord = overview.permissions.includes("change_doctor_prescription");
  const [recording, setRecording] = useState(false);

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card
        title="Medicines"
        className="lg:col-span-2"
        bodyClassName="p-0"
        action={canRecord && <Button size="sm" variant="secondary" onClick={() => setRecording(true)}>Record current medicine</Button>}
      >
        <QueryState
          query={meds}
          what="Medicines"
          isEmpty={(m) => m.length === 0}
          empty={
            <EmptyState
              icon={<Pill className="size-5" />}
              title="No medicines recorded"
              description="Medicines appear here when you issue a prescription or record a medicine the patient already takes."
            />
          }
        >
          {(m) => (
            <ul className="divide-y divide-line">
              {m.map((med) => (
                <li key={med.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <p className="font-medium">
                      {med.name}
                      {med.strength && <span className="font-normal text-muted"> {med.strength}</span>}
                      {med.is_prn && <Badge tone="neutral">When needed</Badge>}
                    </p>
                    {med.instructions && <p className="text-sm text-muted">{med.instructions}</p>}
                    <p className="text-xs text-muted">
                      {med.start_date ? `From ${formatDate(med.start_date)}` : "Start date not recorded"}
                      {med.end_date ? ` to ${formatDate(med.end_date)}` : ""}
                    </p>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <StatusBadge
                      status={med.status}
                      label={med.status === "pending_confirmation" ? "Awaiting patient confirmation" : undefined}
                    />
                    <SourceLabel source={med.source} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </QueryState>
      </Card>

      <Card title="Adherence (last 30 days)">
        {!canAdherence ? (
          <p className="text-sm text-muted">The patient has not shared adherence information with you.</p>
        ) : (
          <QueryState query={adherence} what="Adherence">
            {(a) =>
              a.total_recorded === 0 ? (
                <p className="text-sm text-muted">
                  {a.has_schedules
                    ? "No doses have been recorded yet in this period."
                    : "No dose records yet. Adherence appears once the patient confirms a schedule and starts logging doses."}
                </p>
              ) : (
                <ul className="flex flex-col gap-4">
                  {a.lines.map((l) => (
                    <li key={l.medication_id}>
                      <div className="flex justify-between text-sm">
                        <span className="font-medium">{l.name}</span>
                        <span className="tabular-nums">{l.rate === null ? "—" : `${Math.round(l.rate * 100)}%`}</span>
                      </div>
                      <div
                        className="mt-1 h-2 overflow-hidden rounded-full bg-surface-2"
                        role="img"
                        aria-label={`${l.taken} taken, ${l.skipped} skipped, ${l.missed} missed`}
                      >
                        <div className="h-full bg-success" style={{ width: `${(l.rate ?? 0) * 100}%` }} />
                      </div>
                      <p className="mt-1 text-xs text-muted">
                        {l.taken} taken · {l.skipped} skipped · {l.missed} missed (recorded doses only)
                      </p>
                    </li>
                  ))}
                </ul>
              )
            }
          </QueryState>
        )}
      </Card>
      <RecordMedicationDialog patientId={patientId} open={recording} onClose={() => setRecording(false)} />
    </div>
  );
}

// --- Prescriptions -----------------------------------------------------------------------------

function PrescriptionCard({ rx, patientId, canWrite }: { rx: Prescription; patientId: string; canWrite: boolean }) {
  const issue = useIssuePrescription(patientId);
  const cancel = useCancelPrescription(patientId);
  const toast = useToast();
  const [dialog, setDialog] = useState<"edit" | "issue" | "cancel" | "correct" | null>(null);
  const mine = rx.prescribed_by_me && canWrite;
  const correctionPending = rx.superseded_by_id !== null && rx.status === "issued";

  return (
    <li className="px-4 py-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium">
            {formatDate(rx.prescribed_on)} · {rx.prescriber_name ?? rx.external_prescriber_name ?? "Unknown prescriber"}
            {rx.prescribed_by_me && <span className="font-normal text-muted"> (you)</span>}
          </p>
          {rx.diagnosis_as_written && <p className="text-sm text-muted">Diagnosis: {rx.diagnosis_as_written}</p>}
          {rx.revision > 1 && (
            <p className="text-sm text-muted">
              {rx.status === "draft" ? "Correction draft" : "Corrected"} · version {rx.revision}
              {rx.revision_reason ? ` · ${rx.revision_reason}` : ""}
            </p>
          )}
          {correctionPending && <p className="text-sm text-warning">A correction draft exists for this prescription.</p>}
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={rx.status} />
          <Link to={`/doctor/patients/${patientId}/prescriptions/${rx.id}`} className="text-sm font-medium text-accent underline">
            View
          </Link>
        </div>
      </div>
      <ol className="mt-3 flex flex-col gap-2">
        {rx.items.map((i) => (
          <li key={i.id} className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
            <span className="font-medium">{i.drug_name}</span>
            {i.strength && ` ${i.strength}`}
            {i.dosage_form && ` · ${i.dosage_form}`}
            <span className="block text-muted">
              {[
                i.dose_amount && `${Number(i.dose_amount)} ${i.dose_unit ?? ""}`.trim(),
                i.frequency_text,
                i.meal_relation && humanize(i.meal_relation),
                i.duration_days && `${i.duration_days} days`,
                i.is_prn && `When needed${i.prn_reason ? `: ${i.prn_reason}` : ""}`,
                i.instructions,
              ]
                .filter(Boolean)
                .join(" · ") || "No directions recorded"}
            </span>
          </li>
        ))}
      </ol>
      {rx.advice && <p className="mt-3 text-sm"><span className="text-muted">Advice:</span> {rx.advice}</p>}
      {rx.status === "cancelled" && rx.cancel_reason && (
        <p className="mt-2 text-sm text-muted">Cancelled: {rx.cancel_reason}</p>
      )}
      {mine && (rx.status === "draft" || rx.status === "issued") && (
        <div className="mt-4 flex flex-wrap gap-2">
          {rx.status === "draft" ? (
            <>
              <Button size="sm" onClick={() => setDialog("issue")}>Issue</Button>
              <Button size="sm" variant="secondary" onClick={() => setDialog("edit")}>Edit draft</Button>
              <Button size="sm" variant="ghost" onClick={() => setDialog("cancel")}>Discard</Button>
            </>
          ) : (
            <>
              {!correctionPending && (
                <Button size="sm" variant="secondary" onClick={() => setDialog("correct")}>Correct</Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setDialog("cancel")}>Cancel prescription</Button>
            </>
          )}
        </div>
      )}
      <PrescriptionDialog patientId={patientId} draft={rx} open={dialog === "edit"} onClose={() => setDialog(null)} />
      <PrescriptionDialog patientId={patientId} correcting={rx} open={dialog === "correct"} onClose={() => setDialog(null)} />
      <ReasonDialog
        open={dialog === "issue"}
        title="Issue this prescription?"
        description={
          rx.revision > 1
            ? `This correction becomes version ${rx.revision} and replaces the current version, which stays in the record marked as superseded. Medicines from the previous version stop, and the patient confirms the corrected ones.`
            : "Once issued it cannot be edited (only corrected as a new version). Its medicines are added to the patient's list, and nothing starts until the patient confirms the schedule."
        }
        confirmLabel="Issue prescription"
        requireReason={false}
        onConfirm={async () => {
          await issue.mutateAsync(rx.id);
          toast.success("Prescription issued");
        }}
        onClose={() => setDialog(null)}
      />
      <ReasonDialog
        open={dialog === "cancel"}
        title={rx.status === "draft" ? "Discard this draft?" : "Cancel this prescription?"}
        description={
          rx.status === "draft"
            ? "The draft has not been issued and will be removed."
            : "The prescription stays in the record, marked as cancelled with your reason."
        }
        confirmLabel={rx.status === "draft" ? "Discard draft" : "Cancel prescription"}
        danger
        onConfirm={async (reason) => {
          await cancel.mutateAsync({ prescription_id: rx.id, reason });
          toast.success(rx.status === "draft" ? "Draft discarded" : "Prescription cancelled");
        }}
        onClose={() => setDialog(null)}
      />
    </li>
  );
}

function ScansToCheck({ patientId }: { patientId: string }) {
  const scans = useScans(patientId);
  const waiting = (scans.data ?? []).filter((s) => s.status === "needs_review");
  if (waiting.length === 0) return null;
  return (
    <Card title="Paper prescriptions waiting to be checked" className="mb-6">
      <p className="mb-3 text-sm text-muted">
        Uploaded by the patient or their family and not yet checked. If you check one, it is saved as doctor-verified.
      </p>
      <ul className="divide-y divide-line">
        {waiting.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-3 first:pt-0 last:pb-0">
            <span className="text-sm">
              Added {formatDateTime(s.created_at)} · {s.mode === "ai" ? "read by AI" : "being typed in"}
            </span>
            <Link to={`/doctor/patients/${patientId}/prescriptions/scan/${s.id}`} className="text-sm font-semibold text-accent underline">
              Review
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function PrescriptionsSection({ patientId, canWrite, onNew }: { patientId: string; canWrite: boolean; onNew: () => void }) {
  const rxs = usePrescriptions(patientId);
  return (
    <>
    <ScansToCheck patientId={patientId} />
    <Card title="Prescriptions" bodyClassName="p-0" action={canWrite && <Button size="sm" variant="secondary" onClick={onNew}>New prescription</Button>}>
      <QueryState
        query={rxs}
        what="Prescriptions"
        isEmpty={(r) => r.length === 0}
        empty={
          <EmptyState
            icon={<ClipboardPlus className="size-5" />}
            title="No prescriptions"
            description="Prescriptions you write are saved as drafts first, and issued when you are ready."
            action={canWrite ? <Button variant="secondary" onClick={onNew}>Write a prescription</Button> : undefined}
          />
        }
      >
        {(r) => {
          // Earlier versions stay reachable from each prescription's version history.
          const shown = r.filter((rx) => rx.status !== "superseded");
          const hidden = r.length - shown.length;
          return (
            <>
              <ul className="divide-y divide-line">
                {shown.map((rx) => (
                  <PrescriptionCard key={rx.id} rx={rx} patientId={patientId} canWrite={canWrite} />
                ))}
              </ul>
              {hidden > 0 && (
                <p className="border-t border-line px-4 py-3 text-sm text-muted">
                  {hidden} earlier {hidden === 1 ? "version is" : "versions are"} kept in the history of the corrected prescriptions.
                </p>
              )}
            </>
          );
        }}
      </QueryState>
    </Card>
    </>
  );
}

// --- Tests ---------------------------------------------------------------------------------------

export function TestsSection({ patientId, canOrder, onOrder }: { patientId: string; canOrder: boolean; onOrder: () => void }) {
  const orders = useTestOrders(patientId);
  const cancel = useCancelOrder(patientId);
  const toast = useToast();
  const [cancelling, setCancelling] = useState<string | null>(null);
  return (
    <Card title="Test orders" bodyClassName="p-0" action={canOrder && <Button size="sm" variant="secondary" onClick={onOrder}>Order tests</Button>}>
      <QueryState
        query={orders}
        what="Tests"
        isEmpty={(o) => o.length === 0}
        empty={
          <EmptyState
            icon={<FlaskConical className="size-5" />}
            title="No tests ordered"
            description="Tests you order appear here. Attach results from the Reports tab when they arrive."
            action={canOrder ? <Button variant="secondary" onClick={onOrder}>Order tests</Button> : undefined}
          />
        }
      >
        {(o) => (
          <ul className="divide-y divide-line">
            {o.map((order) => (
              <li key={order.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="font-medium">{order.tests.join(", ")}</p>
                  <p className="text-sm text-muted">
                    Ordered {formatDateTime(order.ordered_at)} by {order.ordering_doctor_name ?? "a doctor"}
                    {order.due_by && ` · needed by ${formatDate(order.due_by)}`}
                  </p>
                  {order.clinical_indication && <p className="text-sm">Indication: {order.clinical_indication}</p>}
                  {order.cancel_reason && <p className="text-sm text-muted">Cancelled: {order.cancel_reason}</p>}
                </div>
                <div className="flex items-center gap-2">
                  {order.priority !== "routine" && <Badge tone="danger">{humanize(order.priority)}</Badge>}
                  <StatusBadge status={order.status} />
                  {canOrder && order.status === "ordered" && (
                    <Button size="sm" variant="ghost" onClick={() => setCancelling(order.id)}>Cancel</Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
      <ReasonDialog
        open={cancelling !== null}
        title="Cancel this test order?"
        description="The order stays in the record, marked as cancelled."
        confirmLabel="Cancel order"
        danger
        onConfirm={async (reason) => {
          if (cancelling) await cancel.mutateAsync({ order_id: cancelling, reason });
          toast.success("Order cancelled");
        }}
        onClose={() => setCancelling(null)}
      />
    </Card>
  );
}

// --- Reports and documents ---------------------------------------------------------------------

export function ReportsSection({ patientId, canUpload, onUpload }: { patientId: string; canUpload: boolean; onUpload: () => void }) {
  const reports = useReports(patientId);
  const documents = useDocuments(patientId);
  const toast = useToast();

  const open = async (documentId: string) => {
    try {
      window.open(await documentDownloadUrl(patientId, documentId), "_blank", "noopener,noreferrer");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <div className="grid gap-6">
      <Card title="Reports" bodyClassName="p-0" action={canUpload && <Button size="sm" variant="secondary" onClick={onUpload}>Upload report</Button>}>
        <QueryState
          query={reports}
          what="Reports"
          isEmpty={(r) => r.length === 0}
          empty={
            <EmptyState
              icon={<TestTube className="size-5" />}
              title="No reports"
              description="Upload a report file and, if you wish, enter its values as printed."
              action={canUpload ? <Button variant="secondary" onClick={onUpload}>Upload a report</Button> : undefined}
            />
          }
        >
          {(r) => (
            <ul className="divide-y divide-line">
              {r.map((rep) => (
                <li key={rep.id} className="px-4 py-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <p className="font-medium">{rep.lab_name ?? "Report"}</p>
                      <p className="text-sm text-muted">
                        {rep.collected_at ? `Collected ${formatDate(rep.collected_at.slice(0, 10))}` : `Added ${formatDate(rep.created_at.slice(0, 10))}`}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusBadge status={rep.status} />
                      {rep.document_id && (
                        <Button size="sm" variant="secondary" icon={<Download className="size-4" />} onClick={() => void open(rep.document_id!)}>
                          File
                        </Button>
                      )}
                    </div>
                  </div>
                  {rep.results.length > 0 && (
                    <div className="mt-3 overflow-x-auto">
                      <table className="w-full min-w-[28rem] text-sm">
                        <thead className="text-left text-muted">
                          <tr>
                            <th scope="col" className="py-1 pr-3 font-medium">Test</th>
                            <th scope="col" className="py-1 pr-3 font-medium">Value</th>
                            <th scope="col" className="py-1 pr-3 font-medium">Reference</th>
                            <th scope="col" className="py-1 font-medium">Flag (as printed)</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-line">
                          {rep.results.map((res) => (
                            <tr key={res.id}>
                              <td className="py-1.5 pr-3">{res.analyte_name}</td>
                              <td className="py-1.5 pr-3 tabular-nums">
                                {res.value_numeric !== null && res.value_numeric !== undefined ? Number(res.value_numeric) : res.value_text} {res.unit}
                              </td>
                              <td className="py-1.5 pr-3 text-muted">
                                {res.reference_text ??
                                  (res.reference_low !== null || res.reference_high !== null
                                    ? `${res.reference_low ?? ""}–${res.reference_high ?? ""}`
                                    : "—")}
                              </td>
                              <td className="py-1.5">{res.flag === "unknown" ? "—" : humanize(res.flag)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {rep.conclusion && <p className="mt-2 text-sm"><span className="text-muted">Conclusion (as written):</span> {rep.conclusion}</p>}
                </li>
              ))}
            </ul>
          )}
        </QueryState>
      </Card>

      <Card title="Documents" bodyClassName="p-0">
        <QueryState
          query={documents}
          what="Documents"
          isEmpty={(d) => d.length === 0}
          empty={<EmptyState icon={<FileText className="size-5" />} title="No documents" description="Files attached to reports and records are listed here." />}
        >
          {(d) => (
            <ul className="divide-y divide-line">
              {d.map((doc) => (
                <li key={doc.id} className="flex items-center justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{doc.title ?? humanize(doc.document_type)}</p>
                    <p className="text-sm text-muted">
                      {formatDate(doc.document_date ?? doc.created_at.slice(0, 10))} · {bytes(doc.size_bytes)} · <SourceLabel source={doc.source} />
                    </p>
                  </div>
                  {doc.scan_status === "clean" ? (
                    <Button size="sm" variant="secondary" icon={<Download className="size-4" />} onClick={() => void open(doc.id)}>
                      Open
                    </Button>
                  ) : (
                    <Badge tone="warning">Checking file</Badge>
                  )}
                </li>
              ))}
            </ul>
          )}
        </QueryState>
      </Card>
    </div>
  );
}
