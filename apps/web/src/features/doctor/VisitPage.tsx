import { ArrowLeft, CheckCircle2, FileText, Lock, PenLine } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Badge, Button, Card, EmptyState, Field, Select, Textarea, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { formatDateTime, humanize } from "@/lib/format";

import {
  useAddNote,
  useAmendNote,
  useCompleteVisit,
  useOverview,
  useSignNote,
  useUpdateNote,
  useVisit,
  type Note,
} from "./api";
import { PrescriptionDialog } from "./forms/prescription";
import { FollowUpDialog } from "./forms/scheduling";
import { OrderTestsDialog } from "./forms/tests";
import { DiagnosisDialog } from "./forms/visit";
import { DefinitionList, QueryState, StatusBadge } from "./shared";
import { ReasonDialog } from "./sections/orders";

const NOTE_TYPES = ["consultation", "soap", "progress", "procedure", "referral", "discharge", "other"] as const;
type NoteType = (typeof NOTE_TYPES)[number];

export function VisitPage() {
  const { patientId = "", visitId = "" } = useParams();
  const visit = useVisit(patientId, visitId);
  const overview = useOverview(patientId);
  const complete = useCompleteVisit(patientId);
  const toast = useToast();
  const [dialog, setDialog] = useState<"diagnosis" | "tests" | "prescription" | "follow-up" | "complete" | null>(null);
  const perms = overview.data?.permissions ?? [];
  const canEdit = perms.includes("edit_clinical_records");

  return (
    <>
      <PageHeader
        back={
          <Link to={`/doctor/patients/${patientId}/visits`} className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
            <ArrowLeft className="size-4" aria-hidden /> {overview.data?.profile?.display_name ?? "Patient"} · Visits
          </Link>
        }
        title="Visit"
        description={visit.data ? `${humanize(visit.data.visit.visit_type)} · ${formatDateTime(visit.data.visit.started_at)}` : undefined}
        actions={
          visit.data?.visit.status === "in_progress" &&
          visit.data.visit.recorded_by_me && (
            <Button variant="secondary" icon={<CheckCircle2 className="size-4" />} onClick={() => setDialog("complete")}>
              Complete visit
            </Button>
          )
        }
      />

      <QueryState query={visit} what="Visit" rows={4}>
        {({ visit: v, notes }) => {
          const inProgress = v.status === "in_progress";
          return (
            <div className="grid gap-6 lg:grid-cols-3">
              <div className="flex flex-col gap-6 lg:col-span-2">
                <Card title="Clinical notes">
                  <div className="flex flex-col gap-4">
                    {notes.length === 0 && !canEdit && (
                      <EmptyState icon={<FileText className="size-5" />} title="No notes for this visit" />
                    )}
                    {notes.map((n) => (
                      <NoteCard key={n.id} note={n} patientId={patientId} canEdit={canEdit} />
                    ))}
                    {canEdit && v.status !== "cancelled" && <NoteComposer patientId={patientId} visitId={v.id} first={notes.length === 0} />}
                  </div>
                </Card>
              </div>

              <div className="flex flex-col gap-6">
                <Card title="Details">
                  <DefinitionList
                    items={[
                      ["Status", <StatusBadge key="s" status={v.status} />],
                      ["Doctor", `${v.doctor_name ?? "—"}${v.recorded_by_me ? " (you)" : ""}`],
                      ["Started", formatDateTime(v.started_at)],
                      ["Ended", formatDateTime(v.ended_at)],
                      ["Location", v.location ?? "—"],
                    ]}
                  />
                  {v.chief_complaint && (
                    <div className="mt-4 border-t border-line pt-4 text-sm">
                      <p className="text-muted">Reason for visit</p>
                      <p className="mt-1 whitespace-pre-line">{v.chief_complaint}</p>
                    </div>
                  )}
                </Card>
                {canEdit && (
                  <Card title="During this visit">
                    <div className="flex flex-col gap-2">
                      <Button variant="secondary" onClick={() => setDialog("diagnosis")}>Record assessment / diagnosis</Button>
                      <Button variant="secondary" onClick={() => setDialog("tests")}>Order tests</Button>
                      {perms.includes("change_doctor_prescription") && (
                        <Button variant="secondary" onClick={() => setDialog("prescription")}>Write prescription</Button>
                      )}
                      <Button variant="secondary" onClick={() => setDialog("follow-up")}>Set follow-up</Button>
                    </div>
                    {!inProgress && <p className="mt-3 text-xs text-muted">This visit is {humanize(v.status).toLowerCase()}; items are still linked to it.</p>}
                  </Card>
                )}
              </div>
            </div>
          );
        }}
      </QueryState>

      <DiagnosisDialog patientId={patientId} visitId={visitId} open={dialog === "diagnosis"} onClose={() => setDialog(null)} />
      <OrderTestsDialog patientId={patientId} visitId={visitId} open={dialog === "tests"} onClose={() => setDialog(null)} />
      <PrescriptionDialog patientId={patientId} visitId={visitId} open={dialog === "prescription"} onClose={() => setDialog(null)} />
      <FollowUpDialog patientId={patientId} visitId={visitId} open={dialog === "follow-up"} onClose={() => setDialog(null)} />
      <ReasonDialog
        open={dialog === "complete"}
        title="Complete this visit?"
        description="Records the end time. Draft notes stay as drafts until you sign them."
        confirmLabel="Complete visit"
        requireReason={false}
        onConfirm={async () => {
          await complete.mutateAsync(visitId);
          toast.success("Visit completed");
        }}
        onClose={() => setDialog(null)}
      />
    </>
  );
}

function NoteComposer({ patientId, visitId, first }: { patientId: string; visitId: string; first: boolean }) {
  const add = useAddNote(patientId);
  const toast = useToast();
  const [body, setBody] = useState("");
  const [type, setType] = useState<NoteType>("consultation");
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!body.trim()) {
      setError("Write the note before saving.");
      return;
    }
    setError(null);
    try {
      await add.mutateAsync({ visit_id: visitId, note_type: type, body });
      setBody("");
      toast.success("Draft saved. Sign it when it is final.");
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <div className="rounded-xl border border-dashed border-line p-4">
      <p className="mb-3 text-sm font-medium">{first ? "Write the first note for this visit" : "Add another note"}</p>
      <div className="flex flex-col gap-3">
        <Field label="Note type">
          {(p) => (
            <Select {...p} value={type} onChange={(e) => setType(e.target.value as NoteType)}>
              {NOTE_TYPES.map((t) => (
                <option key={t} value={t}>{t === "soap" ? "SOAP" : humanize(t)}</option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Note" error={error ?? undefined} hint="Saved as a draft only you can see. Sign it to make it part of the record.">
          {(p) => <Textarea {...p} rows={6} value={body} onChange={(e) => setBody(e.target.value)} />}
        </Field>
        <div>
          <Button onClick={() => void save()} loading={add.isPending}>Save draft</Button>
        </div>
      </div>
    </div>
  );
}

function NoteCard({ note, patientId, canEdit }: { note: Note; patientId: string; canEdit: boolean }) {
  const update = useUpdateNote(patientId);
  const sign = useSignNote(patientId);
  const amend = useAmendNote(patientId);
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [amending, setAmending] = useState(false);
  const [body, setBody] = useState(note.body);
  const [reason, setReason] = useState("");
  const [confirmSign, setConfirmSign] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isDraft = note.status === "draft";
  const mine = note.written_by_me && canEdit;

  const saveDraft = async () => {
    setError(null);
    try {
      await update.mutateAsync({ note_id: note.id, body });
      setEditing(false);
      toast.success("Draft updated");
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const startAmendment = async () => {
    if (reason.trim().length < 3) {
      setError("Say briefly why the note is being amended.");
      return;
    }
    setError(null);
    try {
      await amend.mutateAsync({ note_id: note.id, body, reason });
      setAmending(false);
      setReason("");
      toast.success("Amendment saved as a draft. Sign it to replace the original.");
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <article
      className={
        "rounded-xl border p-4 " +
        (isDraft ? "border-warning/40 bg-warning/5" : note.status === "superseded" ? "border-line opacity-70" : "border-line")
      }
      aria-label={`${humanize(note.note_type)} note`}
    >
      <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">
          <span className="font-medium">{note.note_type === "soap" ? "SOAP" : humanize(note.note_type)} note</span>
          <span className="text-muted">
            {" · "}
            {note.author_name ?? "Doctor"}
            {note.written_by_me && " (you)"} · {formatDateTime(note.signed_at ?? note.created_at)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {note.supersedes_note_id && <Badge tone="info">Amendment</Badge>}
          <StatusBadge status={note.status} label={isDraft ? "Draft, only you can see it" : undefined} />
        </div>
      </header>
      {note.amendment_reason && <p className="mb-2 text-sm text-muted">Reason for amendment: {note.amendment_reason}</p>}

      {editing || amending ? (
        <div className="flex flex-col gap-3">
          <Textarea aria-label="Note text" rows={8} value={body} onChange={(e) => setBody(e.target.value)} />
          {amending && (
            <Field label="Reason for amendment" required>
              {(p) => <Textarea {...p} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
            </Field>
          )}
          {error && <Alert tone="danger">{error}</Alert>}
          <div className="flex gap-2">
            <Button size="sm" loading={update.isPending || amend.isPending} onClick={() => void (amending ? startAmendment() : saveDraft())}>
              {amending ? "Save amendment draft" : "Save"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setAmending(false);
                setBody(note.body);
                setError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <p className="whitespace-pre-line text-sm leading-relaxed">{note.body}</p>
      )}

      {mine && !editing && !amending && (
        <footer className="mt-3 flex flex-wrap gap-2">
          {isDraft ? (
            <>
              <Button size="sm" icon={<Lock className="size-4" />} onClick={() => setConfirmSign(true)}>
                Sign
              </Button>
              <Button size="sm" variant="secondary" icon={<PenLine className="size-4" />} onClick={() => setEditing(true)}>
                Edit
              </Button>
            </>
          ) : (
            note.status === "signed" && (
              <Button size="sm" variant="secondary" icon={<PenLine className="size-4" />} onClick={() => setAmending(true)}>
                Amend
              </Button>
            )
          )}
        </footer>
      )}

      <ReasonDialog
        open={confirmSign}
        title="Sign this note?"
        description="A signed note becomes part of the permanent record and cannot be edited. Corrections are made by amendment, which keeps the original."
        confirmLabel="Sign note"
        requireReason={false}
        onConfirm={async () => {
          await sign.mutateAsync(note.id);
          toast.success("Note signed");
        }}
        onClose={() => setConfirmSign(false)}
      />
    </article>
  );
}
