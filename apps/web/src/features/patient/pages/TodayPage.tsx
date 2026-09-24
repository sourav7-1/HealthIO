import { CalendarDays, CheckCircle2, ClipboardList, FileText, Pill, Stethoscope } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Button, Card, Checkbox, Dialog, EmptyState, useToast } from "@/components/ui";
import { useMe } from "@/features/auth/session";
import { useMissedReminders } from "@/features/reminders/api";
import { useAdherence, useAppointments, useFollowUps, useMedications, usePrescriptions, useReports } from "@/features/chart/api";
import { QueryState, StatusBadge } from "@/features/chart/shared";
import { DATA_CATEGORIES } from "@/features/chart/consent";
import { errorMessage, type Schemas } from "@/lib/api";
import { formatDate, formatDateTime, humanize, isInPast } from "@/lib/format";

import { TODO_DOSE, useDoctorRequests, useDoses, useRespondToDoctor, type ConnectionRequest } from "../api";
import { DoseCard } from "../components";
import { usePatientId } from "../context";

export function TodayPage() {
  const me = useMe();
  const pid = usePatientId();
  const doses = useDoses(pid);
  const missed = useMissedReminders(pid);
  const guidance = new Map((missed.data ?? []).flatMap((r) => (r.guidance ? [[r.dose_id, r.guidance] as const] : [])));
  const meds = useMedications(pid);
  const pending = (meds.data ?? []).filter((m) => m.status === "pending_confirmation");
  const firstName = me.display_name.split(" ")[0];

  return (
    <>
      <PageHeader
        title={`Hello, ${firstName}`}
        description={new Date().toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}
      />

      <DoctorRequests />

      {pending.length > 0 && (
        <div className="mb-6">
          <Alert tone="warning" title={`${pending.length} new medicine${pending.length > 1 ? "s" : ""} from your doctor`}>
            Choose when you want to be reminded to start them.{" "}
            <Link to="/patient/medications" className="font-semibold underline">
              Set up reminders
            </Link>
          </Alert>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="lg:col-span-2" aria-labelledby="today-heading">
          <h2 id="today-heading" className="mb-3 text-lg font-semibold">
            Today&apos;s medicines
          </h2>
          <QueryState
            query={doses}
            what="Medicines"
            isEmpty={(d) => d.length === 0}
            empty={
              <Card>
                <EmptyState
                  icon={<Pill className="size-5" />}
                  title="No medicines scheduled today"
                  description="Medicines appear here once you set reminder times for them."
                  action={
                    <Link to="/patient/medications" className="text-sm font-semibold text-accent underline">
                      Go to my medicines
                    </Link>
                  }
                />
              </Card>
            }
          >
            {(list) => {
              const todo = list.filter((d) => TODO_DOSE.includes(d.status));
              const done = list.filter((d) => !TODO_DOSE.includes(d.status));
              return (
                <div className="flex flex-col gap-3">
                  {todo.length === 0 && (
                    <Alert tone="success">
                      <span className="flex items-center gap-2">
                        <CheckCircle2 className="size-4" aria-hidden /> All done for today.
                      </span>
                    </Alert>
                  )}
                  {todo.map((d) => (
                    <DoseCard key={d.id} dose={d} patientId={pid} guidance={guidance.get(d.id)} />
                  ))}
                  {done.length > 0 && (
                    <details className="rounded-xl border border-line bg-surface p-4">
                      <summary className="cursor-pointer text-sm font-semibold">Done today ({done.length})</summary>
                      <div className="mt-3 flex flex-col gap-3">
                        {done.map((d) => (
                          <DoseCard key={d.id} dose={d} patientId={pid} />
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              );
            }}
          </QueryState>
          <UpcomingDoses patientId={pid} />
        </section>

        <div className="flex flex-col gap-6">
          <AdherenceCard patientId={pid} />
          <UpcomingAppointments patientId={pid} />
          <FollowUpReminders patientId={pid} />
        </div>

        <RecentPrescriptions patientId={pid} />
        <RecentReports patientId={pid} />
      </div>
    </>
  );
}

function UpcomingDoses({ patientId }: { patientId: string }) {
  const tomorrow = useDoses(patientId, 2);
  const today = new Date().toDateString();
  const upcoming = (tomorrow.data ?? []).filter(
    (d) => d.scheduled_at && new Date(d.scheduled_at).toDateString() !== today && d.status === "scheduled",
  );
  if (upcoming.length === 0) return null;
  return (
    <Card title="Tomorrow" className="mt-6">
      <ul className="divide-y divide-line">
        {upcoming.map((d) => (
          <li key={d.id} className="flex justify-between gap-3 py-2 text-sm first:pt-0 last:pb-0">
            <span className="font-medium">{d.medication_name}</span>
            <span className="tabular-nums text-muted">{formatDateTime(d.scheduled_at)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function AdherenceCard({ patientId }: { patientId: string }) {
  const adherence = useAdherence(patientId);
  return (
    <Card title="Last 30 days">
      <QueryState query={adherence} what="Adherence" rows={1}>
        {(a) => {
          const taken = a.lines.reduce((n, l) => n + l.taken, 0);
          const total = a.total_recorded;
          if (total === 0) {
            return <p className="text-sm text-muted">Your record will appear here after you mark your first doses.</p>;
          }
          const pct = Math.round((taken / total) * 100);
          return (
            <div>
              <p className="text-3xl font-semibold tabular-nums">{pct}%</p>
              <p className="text-sm text-muted">
                of recorded doses taken ({taken} of {total})
              </p>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-surface-2" aria-hidden>
                <div className="h-full bg-success" style={{ width: `${pct}%` }} />
              </div>
            </div>
          );
        }}
      </QueryState>
    </Card>
  );
}

function UpcomingAppointments({ patientId }: { patientId: string }) {
  const appts = useAppointments(patientId);
  return (
    <Card title="Appointments" action={<Link to="/patient/appointments" className="text-sm font-medium text-accent underline">All</Link>}>
      <QueryState query={appts} what="Appointments" rows={2}>
        {(list) => {
          const upcoming = list
            .filter((a) => !isInPast(a.starts_at) && ["requested", "scheduled", "confirmed"].includes(a.status))
            .sort((a, b) => a.starts_at.localeCompare(b.starts_at))
            .slice(0, 3);
          return upcoming.length === 0 ? (
            <p className="text-sm text-muted">No upcoming appointments.</p>
          ) : (
            <ul className="flex flex-col gap-3">
              {upcoming.map((a) => (
                <li key={a.id} className="flex items-start gap-3 text-sm">
                  <CalendarDays className="mt-0.5 size-4 text-muted" aria-hidden />
                  <div>
                    <p className="font-medium">{formatDateTime(a.starts_at)}</p>
                    <p className="text-muted">
                      {a.doctor_name ?? "Your doctor"} · {humanize(a.mode)}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          );
        }}
      </QueryState>
    </Card>
  );
}

function FollowUpReminders({ patientId }: { patientId: string }) {
  const followUps = useFollowUps(patientId);
  return (
    <Card title="Follow-ups">
      <QueryState query={followUps} what="Follow-ups" rows={1}>
        {(list) => {
          const open = list.filter((f) => f.status === "open").sort((a, b) => a.due_date.localeCompare(b.due_date));
          return open.length === 0 ? (
            <p className="text-sm text-muted">Nothing to follow up on.</p>
          ) : (
            <ul className="flex flex-col gap-3">
              {open.map((f) => (
                <li key={f.id} className="flex items-start gap-3 text-sm">
                  <ClipboardList className="mt-0.5 size-4 text-muted" aria-hidden />
                  <div>
                    <p className="font-medium">See {f.doctor_name ?? "your doctor"} by {formatDate(f.due_date)}</p>
                    {f.reason && <p className="text-muted">{f.reason}</p>}
                  </div>
                </li>
              ))}
            </ul>
          );
        }}
      </QueryState>
    </Card>
  );
}

function RecentPrescriptions({ patientId }: { patientId: string }) {
  const rxs = usePrescriptions(patientId);
  return (
    <Card title="Recent prescriptions" className="lg:col-span-2" action={<Link to="/patient/prescriptions" className="text-sm font-medium text-accent underline">All</Link>}>
      <QueryState query={rxs} what="Prescriptions" rows={2} isEmpty={(r) => r.length === 0} empty={<p className="text-sm text-muted">No prescriptions yet.</p>}>
        {(list) => (
          <ul className="divide-y divide-line">
            {list.filter((rx) => rx.status !== "superseded").slice(0, 3).map((rx) => (
              <li key={rx.id} className="flex flex-wrap items-center justify-between gap-2 py-3 first:pt-0 last:pb-0">
                <div className="min-w-0 text-sm">
                  <p className="font-medium">{rx.items.map((i) => i.drug_name).join(", ")}</p>
                  <p className="text-muted">
                    <Stethoscope className="mr-1 inline size-3.5" aria-hidden />
                    {rx.prescriber_name ?? rx.external_prescriber_name ?? "Doctor"} · {formatDate(rx.prescribed_on)}
                  </p>
                </div>
                <StatusBadge status={rx.status} />
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Card>
  );
}

function RecentReports({ patientId }: { patientId: string }) {
  const reports = useReports(patientId);
  return (
    <Card title="Recent reports" action={<Link to="/patient/tests" className="text-sm font-medium text-accent underline">All</Link>}>
      <QueryState query={reports} what="Reports" rows={2} isEmpty={(r) => r.length === 0} empty={<p className="text-sm text-muted">No reports yet.</p>}>
        {(list) => (
          <ul className="flex flex-col gap-3">
            {list.slice(0, 3).map((r) => (
              <li key={r.id} className="flex items-start gap-3 text-sm">
                <FileText className="mt-0.5 size-4 text-muted" aria-hidden />
                <div>
                  <p className="font-medium">{r.lab_name ?? "Report"}</p>
                  <p className="text-muted">{formatDate((r.collected_at ?? r.created_at).slice(0, 10))}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Card>
  );
}

// --- Doctor connection requests ---------------------------------------------------------------

function DoctorRequests() {
  const requests = useDoctorRequests();
  const [open, setOpen] = useState<ConnectionRequest | null>(null);
  if (!requests.data?.length) return null;
  return (
    <div className="mb-6 flex flex-col gap-3">
      {requests.data.map((r) => (
        <Alert key={r.relationship_id} tone="info" title={`${r.doctor_name} wants to connect with you`}>
          <p>
            {r.primary_specialty ? `${r.primary_specialty}. ` : ""}Nothing is shared until you choose what they can see.
          </p>
          <Button size="sm" className="mt-2" onClick={() => setOpen(r)}>
            Review request
          </Button>
        </Alert>
      ))}
      {open && <RespondDialog request={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function RespondDialog({ request, onClose }: { request: ConnectionRequest; onClose: () => void }) {
  const respond = useRespondToDoctor();
  const toast = useToast();
  const [chosen, setChosen] = useState<string[]>(["demographics", "conditions", "allergies", "medications", "prescriptions", "visits_and_notes", "tests_and_reports", "appointments"]);
  const [error, setError] = useState<string | null>(null);

  const submit = async (decision: "accept" | "decline") => {
    if (decision === "accept" && chosen.length === 0) {
      setError("Choose at least one type of information, or decline.");
      return;
    }
    try {
      await respond.mutateAsync({
        relationship_id: request.relationship_id,
        decision,
        data_categories: chosen as Schemas["DataCategory"][],
      });
      toast.success(decision === "accept" ? `You are now connected with ${request.doctor_name}` : "Request declined");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Connect with ${request.doctor_name}?`}
      description="Choose what this doctor can see. You can change this later."
      footer={
        <>
          <Button variant="secondary" onClick={() => void submit("decline")} loading={respond.isPending}>
            Decline
          </Button>
          <Button onClick={() => void submit("accept")} loading={respond.isPending}>
            Accept and share
          </Button>
        </>
      }
    >
      {error && (
        <div className="mb-3">
          <Alert tone="danger">{error}</Alert>
        </div>
      )}
      <div className="flex flex-col gap-3">
        {DATA_CATEGORIES.map((c) => (
          <Checkbox
            key={c.value}
            label={c.label}
            description={c.description}
            checked={chosen.includes(c.value)}
            onChange={(e) =>
              setChosen((prev) => (e.target.checked ? [...prev, c.value] : prev.filter((v) => v !== c.value)))
            }
          />
        ))}
      </div>
    </Dialog>
  );
}
