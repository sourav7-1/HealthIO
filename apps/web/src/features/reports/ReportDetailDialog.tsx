/**
 * One report: its details, the file, values exactly as printed, review status, and, for
 * whoever manages the patient's sharing, which doctors it is shared with. Nothing here
 * interprets a result; patients are pointed to their doctor for what results mean.
 */
import { Check, Download, Share2, Trash2, Undo2, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Alert, Badge, Button, Dialog, Field, Input, Select, Textarea, useToast } from "@/components/ui";
import { DefinitionList, QueryState, StatusBadge } from "@/features/chart/shared";
import { errorMessage } from "@/lib/api";
import { bytes, formatDate, formatDateTime, humanize } from "@/lib/format";

import {
  reportFileUrl,
  sourceLabel,
  useReport,
  useReportInError,
  useReviewReport,
  useRevokeShare,
  useShareReport,
  type ReportDetail,
  type ReportSummary,
} from "./api";

type Viewer = "patient" | "doctor";

export function ResultsTable({ results, viewer }: { results: ReportSummary["results"]; viewer: Viewer }) {
  if (results.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[24rem] text-sm">
        <thead className="text-left text-muted">
          <tr>
            <th scope="col" className="py-1 pr-3 font-medium">Test</th>
            <th scope="col" className="py-1 pr-3 font-medium">Result</th>
            <th scope="col" className="py-1 pr-3 font-medium">{viewer === "patient" ? "Range on the report" : "Reference"}</th>
            <th scope="col" className="py-1 font-medium">Flag (as printed)</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {results.map((x) => (
            <tr key={x.id}>
              <td className="py-1.5 pr-3">{x.analyte_name}</td>
              <td className="py-1.5 pr-3 tabular-nums">
                {x.value_numeric != null ? Number(x.value_numeric) : x.value_text} {x.unit}
              </td>
              <td className="py-1.5 pr-3 text-muted">
                {x.reference_text ??
                  (x.reference_low != null || x.reference_high != null ? `${x.reference_low ?? ""}–${x.reference_high ?? ""}` : "—")}
              </td>
              <td className="py-1.5">{x.flag === "unknown" ? "—" : humanize(x.flag)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ReportDetailDialog({
  patientId,
  reportId,
  viewer,
  self = false,
  canReview = false,
  canWithdraw = false,
  onClose,
}: {
  patientId: string;
  reportId: string;
  viewer: Viewer;
  self?: boolean;
  /** Doctor: verify or reject uploaded reports, mark any report entered in error. */
  canReview?: boolean;
  /** Patient side: withdraw an upload that is still waiting for review. */
  canWithdraw?: boolean;
  onClose: () => void;
}) {
  const report = useReport(patientId, reportId);
  const toast = useToast();
  const open = async () => {
    try {
      window.open(await reportFileUrl(patientId, reportId), "_blank", "noopener,noreferrer");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <Dialog open onClose={onClose} size="lg" title={report.data?.test_name ?? "Report"} footer={<Button variant="secondary" onClick={onClose}>Close</Button>}>
      <QueryState query={report} what="Report">
        {(r) => (
          <div className="flex flex-col gap-5">
            {r.access === "shared" && <Alert tone="info">The patient shared this report with you. You can see only the reports they share.</Alert>}
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={r.status} label={r.status === "pending_review" ? "Waiting for a doctor's review" : undefined} />
              <Badge tone={r.source === "doctor" ? "info" : "warning"}>{sourceLabel(r.source, self)}</Badge>
              {r.file && (
                <Button size="sm" variant="secondary" icon={<Download className="size-4" />} disabled={!r.file.available} onClick={() => void open()}>
                  {r.file.available ? `Open file (${bytes(r.file.size_bytes)})` : "File being checked"}
                </Button>
              )}
            </div>
            <DefinitionList
              items={
                [
                  ["Test", r.test_name ?? "—"],
                  ["Date on report", formatDate(r.report_date ?? r.collected_at?.slice(0, 10) ?? null)],
                  ["Laboratory", r.lab_name ?? "—"],
                  ["Report number", r.lab_reference ?? "—"],
                  ["Ordered by", r.ordering_doctor_name ?? (r.order_id ? "A doctor" : "Not linked to an order")],
                  ["Added", formatDateTime(r.created_at)],
                  r.reviewed_at ? ["Reviewed", formatDateTime(r.reviewed_at)] : null,
                ].filter(Boolean) as [string, ReactNode][]
              }
            />
            {r.notes && (
              <p className="text-sm">
                <span className="text-muted">Notes: </span>
                {r.notes}
              </p>
            )}
            {r.review_note && (
              <p className="text-sm">
                <span className="text-muted">{r.status === "rejected" ? "Why it was not accepted: " : r.status === "entered_in_error" ? "Withdrawn: " : "Review note: "}</span>
                {r.review_note}
              </p>
            )}
            <ResultsTable results={r.results} viewer={viewer} />
            {r.conclusion && (
              <p className="text-sm">
                <span className="text-muted">Conclusion (as written on the report): </span>
                {r.conclusion}
              </p>
            )}
            {viewer === "patient" && (
              <p className="text-xs text-muted">
                Health Io shows reports exactly as they were added and never interprets them. Ask your doctor what your results mean for you.
              </p>
            )}
            {canReview && <ReviewActions patientId={patientId} report={r} />}
            {canWithdraw && r.status === "pending_review" && r.source !== "doctor" && <WithdrawAction patientId={patientId} reportId={r.id} />}
            {r.shares && r.share_targets && <SharePanel patientId={patientId} report={r} />}
          </div>
        )}
      </QueryState>
    </Dialog>
  );
}

function ReasonForm({
  label,
  confirm,
  danger,
  required = true,
  onSubmit,
  onCancel,
}: {
  label: string;
  confirm: string;
  danger?: boolean;
  required?: boolean;
  onSubmit: (reason: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const go = async () => {
    if (required && reason.trim().length < 3) return setError("Add a short reason.");
    setBusy(true);
    try {
      await onSubmit(reason.trim());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
      <Field label={label} error={error ?? undefined}>
        {(p) => <Textarea {...p} rows={2} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />}
      </Field>
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>Back</Button>
        <Button size="sm" variant={danger ? "danger" : "primary"} loading={busy} onClick={() => void go()}>
          {confirm}
        </Button>
      </div>
    </div>
  );
}

function ReviewActions({ patientId, report }: { patientId: string; report: ReportDetail }) {
  const review = useReviewReport(patientId);
  const inError = useReportInError(patientId);
  const toast = useToast();
  const [mode, setMode] = useState<"reject" | "error" | null>(null);
  const pending = report.status === "pending_review";
  if (!pending && report.status !== "verified") return null;
  if (mode === "reject")
    return (
      <ReasonForm
        label="Why is this report not accepted? (the patient sees this)"
        confirm="Reject report"
        danger
        onCancel={() => setMode(null)}
        onSubmit={async (note) => {
          await review.mutateAsync({ report_id: report.id, decision: "reject", note });
          toast.success("Report rejected");
          setMode(null);
        }}
      />
    );
  if (mode === "error")
    return (
      <ReasonForm
        label="Why was this report entered in error?"
        confirm="Mark entered in error"
        danger
        onCancel={() => setMode(null)}
        onSubmit={async (reason) => {
          await inError.mutateAsync({ report_id: report.id, reason });
          toast.success("Marked as entered in error");
          setMode(null);
        }}
      />
    );
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-surface-2 p-3">
      {pending && (
        <p className="text-sm">
          Check that this file is this patient's report and that the details match it. Verifying is not an interpretation of the results.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {pending && (
          <>
            <Button
              size="sm"
              icon={<Check className="size-4" />}
              loading={review.isPending}
              onClick={async () => {
                try {
                  await review.mutateAsync({ report_id: report.id, decision: "verify" });
                  toast.success("Report verified");
                } catch (err) {
                  toast.error(errorMessage(err));
                }
              }}
            >
              Verify
            </Button>
            <Button size="sm" variant="secondary" icon={<X className="size-4" />} onClick={() => setMode("reject")}>
              Reject
            </Button>
          </>
        )}
        <Button size="sm" variant="ghost" icon={<Trash2 className="size-4" />} onClick={() => setMode("error")}>
          Entered in error
        </Button>
      </div>
    </div>
  );
}

function WithdrawAction({ patientId, reportId }: { patientId: string; reportId: string }) {
  const inError = useReportInError(patientId);
  const toast = useToast();
  const [open, setOpen] = useState(false);
  if (!open)
    return (
      <div>
        <Button size="sm" variant="ghost" icon={<Undo2 className="size-4" />} onClick={() => setOpen(true)}>
          Withdraw this upload
        </Button>
      </div>
    );
  return (
    <ReasonForm
      label="Why are you withdrawing it? (for example, wrong file)"
      confirm="Withdraw"
      danger
      onCancel={() => setOpen(false)}
      onSubmit={async (reason) => {
        await inError.mutateAsync({ report_id: reportId, reason });
        toast.success("Upload withdrawn. It stays in your record, marked as withdrawn.");
        setOpen(false);
      }}
    />
  );
}

function SharePanel({ patientId, report }: { patientId: string; report: ReportDetail }) {
  const share = useShareReport(patientId);
  const revoke = useRevokeShare(patientId);
  const toast = useToast();
  const [doctorId, setDoctorId] = useState("");
  const [until, setUntil] = useState("");
  const shares = report.shares ?? [];
  const targets = (report.share_targets ?? []).filter((t) => !shares.some((s) => s.doctor_id === t.doctor_id));
  const closed = report.status === "rejected" || report.status === "entered_in_error";

  const add = async () => {
    if (!doctorId) return;
    try {
      await share.mutateAsync({
        report_id: report.id,
        doctor_id: doctorId,
        expires_at: until ? new Date(`${until}T23:59:59`).toISOString() : null,
      });
      toast.success("Report shared");
      setDoctorId("");
      setUntil("");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <section aria-labelledby="share-heading" className="flex flex-col gap-3 border-t border-line pt-4">
      <h3 id="share-heading" className="flex items-center gap-2 font-semibold">
        <Share2 className="size-4" aria-hidden /> Sharing
      </h3>
      <p className="text-sm text-muted">
        Doctors who can see your tests and reports already see this report. Share it here with another of your doctors to show them only this report.
      </p>
      {shares.length > 0 && (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {shares.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
              <span>
                {s.doctor_name ?? "A doctor"}
                <span className="text-muted"> · {s.expires_at ? `until ${formatDate(s.expires_at.slice(0, 10))}` : "no end date"}</span>
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  try {
                    await revoke.mutateAsync(s.id);
                    toast.success("Sharing stopped");
                  } catch (err) {
                    toast.error(errorMessage(err));
                  }
                }}
              >
                Stop sharing
              </Button>
            </li>
          ))}
        </ul>
      )}
      {closed ? null : targets.length === 0 ? (
        shares.length === 0 && <p className="text-sm text-muted">Connect with a doctor first to share reports with them.</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
          <Field label="Share with">
            {(p) => (
              <Select {...p} value={doctorId} onChange={(e) => setDoctorId(e.target.value)}>
                <option value="">Choose a doctor</option>
                {targets.map((t) => (
                  <option key={t.doctor_id} value={t.doctor_id}>
                    {t.name}
                    {t.specialty ? ` (${t.specialty})` : ""}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Until (optional)">
            {(p) => <Input {...p} type="date" min={new Date().toISOString().slice(0, 10)} value={until} onChange={(e) => setUntil(e.target.value)} />}
          </Field>
          <Button onClick={() => void add()} loading={share.isPending} disabled={!doctorId}>
            Share
          </Button>
        </div>
      )}
    </section>
  );
}
