import {
  CalendarClock,
  CalendarDays,
  ClipboardPlus,
  FileText,
  FlaskConical,
  History,
  NotebookPen,
  Pill,
  Stethoscope,
  TestTube,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { Badge, Button, Card, EmptyState, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { formatDate, formatDateTime, humanize, isInPast } from "@/lib/format";

import {
  useAppointments,
  useCloseFollowUp,
  useFollowUps,
  useMedicalHistory,
  useTimeline,
  useVisits,
  type Overview,
  type TimelineEvent,
} from "../api";
import { DiagnosisDialog } from "../forms/visit";
import { DefinitionList, QueryState, SourceLabel, StatusBadge } from "../shared";

// --- Overview --------------------------------------------------------------------------------

export function OverviewSection({ patientId, overview }: { patientId: string; overview: Overview }) {
  const perms = overview.permissions;
  const history = useMedicalHistory(patientId, perms.includes("view_medical_history"));
  const [recording, setRecording] = useState(false);

  const summary: [string, ReactNode][] = [];
  if (overview.last_visit !== null || perms.includes("view_visits")) summary.push(["Last visit", formatDateTime(overview.last_visit)]);
  if (overview.active_medications !== null) summary.push(["Active medicines", overview.active_medications]);
  if (overview.open_test_orders !== null) summary.push(["Open test orders", overview.open_test_orders]);
  if (perms.includes("view_appointments")) {
    summary.push(["Next appointment", formatDateTime(overview.next_appointment)]);
    summary.push(["Next follow-up", formatDate(overview.next_follow_up)]);
  }

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card title="At a glance" className="lg:col-span-1">
        {summary.length ? (
          <DefinitionList items={summary} />
        ) : (
          <p className="text-sm text-muted">This patient has shared only limited information with you.</p>
        )}
      </Card>

      <Card
        title="Problem list"
        className="lg:col-span-2"
        action={
          perms.includes("edit_clinical_records") && (
            <Button size="sm" variant="secondary" icon={<NotebookPen className="size-4" />} onClick={() => setRecording(true)}>
              Record assessment
            </Button>
          )
        }
      >
        {perms.includes("view_medical_history") ? (
          <QueryState
            query={history}
            what="Medical history"
            isEmpty={(h) => h.conditions.length === 0 && h.allergies.length === 0 && h.history.length === 0}
            empty={
              <EmptyState
                icon={<History className="size-5" />}
                title="No conditions or allergies recorded"
                description="Assessments you record appear here, with who recorded them and how certain they are."
              />
            }
          >
            {(h) => (
              <div className="flex flex-col gap-5">
                {h.conditions.length > 0 && (
                  <ul className="divide-y divide-line">
                    {h.conditions.map((c) => (
                      <li key={c.id} className="flex flex-wrap items-start justify-between gap-2 py-3 first:pt-0">
                        <div className="min-w-0">
                          <p className="font-medium">
                            {c.name}
                            {c.icd10_code && <span className="ml-2 text-sm text-muted">{c.icd10_code}</span>}
                          </p>
                          <p className="text-sm text-muted">
                            {humanize(c.clinical_status)}
                            {c.onset_date && ` · since ${formatDate(c.onset_date)}`} · recorded {formatDate(c.recorded_at)}
                          </p>
                          {c.notes && <p className="mt-1 whitespace-pre-line text-sm">{c.notes}</p>}
                        </div>
                        <div className="flex flex-col items-end gap-1">
                          <StatusBadge status={c.verification_status} />
                          <SourceLabel source={c.source} />
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                <div>
                  <h3 className="mb-2 text-sm font-semibold">Allergies</h3>
                  {h.allergies.length === 0 ? (
                    <p className="text-sm text-muted">No allergies recorded. This does not mean the patient has none.</p>
                  ) : (
                    <ul className="flex flex-wrap gap-2">
                      {h.allergies.map((a) => (
                        <li key={a.id}>
                          <Badge tone={a.severity === "severe" || a.severity === "life_threatening" ? "danger" : "warning"}>
                            {a.substance}
                            {a.reaction ? ` — ${a.reaction}` : ""}
                          </Badge>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </QueryState>
        ) : (
          <p className="text-sm text-muted">Medical history is not shared with you.</p>
        )}
      </Card>

      <Card title="Recent activity" className="lg:col-span-3" action={<Link to={`/doctor/patients/${patientId}/timeline`} className="text-sm font-medium text-accent hover:underline">Full timeline</Link>}>
        <RecentActivity patientId={patientId} />
      </Card>

      <DiagnosisDialog patientId={patientId} open={recording} onClose={() => setRecording(false)} />
    </div>
  );
}

function RecentActivity({ patientId }: { patientId: string }) {
  const timeline = useTimeline(patientId);
  return (
    <QueryState
      query={timeline}
      what="Activity"
      isEmpty={(t) => t.length === 0}
      empty={
        <EmptyState
          icon={<History className="size-5" />}
          title="Nothing recorded yet"
          description="Visits, prescriptions, tests and appointments will appear here as they are recorded."
        />
      }
    >
      {(t) => <TimelineList events={t.slice(0, 6)} patientId={patientId} />}
    </QueryState>
  );
}

// --- Timeline ----------------------------------------------------------------------------------

const KIND: Record<string, { label: string; icon: ReactNode }> = {
  visit: { label: "Visits", icon: <Stethoscope className="size-4" /> },
  note: { label: "Notes", icon: <FileText className="size-4" /> },
  diagnosis: { label: "Assessments", icon: <NotebookPen className="size-4" /> },
  prescription: { label: "Prescriptions", icon: <ClipboardPlus className="size-4" /> },
  medication: { label: "Medicines", icon: <Pill className="size-4" /> },
  test_order: { label: "Test orders", icon: <FlaskConical className="size-4" /> },
  report: { label: "Reports", icon: <TestTube className="size-4" /> },
  appointment: { label: "Appointments", icon: <CalendarDays className="size-4" /> },
  follow_up: { label: "Follow-ups", icon: <CalendarClock className="size-4" /> },
};

function eventLink(e: TimelineEvent, patientId: string): string | null {
  const base = `/doctor/patients/${patientId}`;
  switch (e.kind) {
    case "visit":
    case "note":
      return e.resource_id ? `${base}/visits/${e.resource_id}` : null;
    case "prescription":
      return `${base}/prescriptions`;
    case "medication":
      return `${base}/medications`;
    case "test_order":
      return `${base}/tests`;
    case "report":
      return `${base}/reports`;
    case "appointment":
      return `${base}/appointments`;
    case "follow_up":
      return `${base}/follow-ups`;
    default:
      return null;
  }
}

function TimelineList({ events, patientId }: { events: TimelineEvent[]; patientId: string }) {
  return (
    <ol className="relative flex flex-col gap-4 border-l border-line pl-6">
      {events.map((e, i) => {
        const kind = KIND[e.kind];
        const to = eventLink(e, patientId);
        const body = (
          <>
            <p className="font-medium">{e.title}</p>
            {e.detail && <p className="mt-0.5 line-clamp-2 text-sm text-muted">{e.detail}</p>}
          </>
        );
        return (
          <li key={`${e.kind}-${e.resource_id ?? i}-${e.at}`} className="relative">
            <span className="absolute -left-[2.05rem] flex size-7 items-center justify-center rounded-full border border-line bg-surface text-muted" aria-hidden>
              {kind?.icon}
            </span>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">{to ? <Link to={to} className="hover:underline">{body}</Link> : body}</div>
              <div className="flex shrink-0 items-center gap-2">
                <StatusBadge status={e.status} />
                <time dateTime={e.at} className="text-xs text-muted tabular-nums">
                  {e.kind === "follow_up" ? formatDate(e.at.slice(0, 10)) : formatDateTime(e.at)}
                </time>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function TimelineSection({ patientId }: { patientId: string }) {
  const timeline = useTimeline(patientId);
  const [filter, setFilter] = useState<string | null>(null);
  const kinds = useMemo(() => Array.from(new Set((timeline.data ?? []).map((e) => e.kind))), [timeline.data]);

  return (
    <Card>
      <QueryState
        query={timeline}
        what="Timeline"
        rows={6}
        isEmpty={(t) => t.length === 0}
        empty={
          <EmptyState
            icon={<History className="size-5" />}
            title="The timeline is empty"
            description="Everything recorded for this patient that you are allowed to see appears here in date order."
          />
        }
      >
        {(t) => (
          <>
            {kinds.length > 1 && (
              <div className="mb-5 flex flex-wrap gap-2" role="group" aria-label="Filter timeline">
                <FilterChip active={filter === null} onClick={() => setFilter(null)}>All</FilterChip>
                {kinds.map((k) => (
                  <FilterChip key={k} active={filter === k} onClick={() => setFilter(k)}>
                    {KIND[k]?.label ?? humanize(k)}
                  </FilterChip>
                ))}
              </div>
            )}
            <TimelineList events={filter ? t.filter((e) => e.kind === filter) : t} patientId={patientId} />
          </>
        )}
      </QueryState>
    </Card>
  );
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={
        "min-h-9 rounded-full border px-3 text-sm " +
        (active ? "border-accent bg-accent text-accent-fg" : "border-line text-muted hover:text-fg")
      }
    >
      {children}
    </button>
  );
}

// --- Visits --------------------------------------------------------------------------------------

export function VisitsSection({ patientId, canRecord, onRecord }: { patientId: string; canRecord: boolean; onRecord: () => void }) {
  const visits = useVisits(patientId);
  return (
    <Card bodyClassName="p-0">
      <QueryState
        query={visits}
        what="Visits"
        isEmpty={(v) => v.length === 0}
        empty={
          <EmptyState
            icon={<Stethoscope className="size-5" />}
            title="No visits recorded"
            description="Record a visit to add clinical notes, assessments, test orders and prescriptions to it."
            action={canRecord ? <Button variant="secondary" onClick={onRecord}>Record a visit</Button> : undefined}
          />
        }
      >
        {(v) => (
          <ul className="divide-y divide-line">
            {v.map((visit) => (
              <li key={visit.id}>
                <Link to={`/doctor/patients/${patientId}/visits/${visit.id}`} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 hover:bg-surface-2">
                  <div className="min-w-0">
                    <p className="font-medium">
                      {formatDateTime(visit.started_at)} · {humanize(visit.visit_type)}
                    </p>
                    <p className="truncate text-sm text-muted">
                      {visit.doctor_name ?? "Doctor"}
                      {visit.recorded_by_me && " (you)"}
                      {visit.chief_complaint ? ` · ${visit.chief_complaint}` : ""}
                    </p>
                  </div>
                  <StatusBadge status={visit.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Card>
  );
}

// --- Appointments --------------------------------------------------------------------------------

export function AppointmentsSection({ patientId, canBook, onBook }: { patientId: string; canBook: boolean; onBook: () => void }) {
  const appts = useAppointments(patientId);
  return (
    <Card
      bodyClassName="p-0"
      title="Appointments"
      action={canBook && <Button size="sm" variant="secondary" onClick={onBook}>Book</Button>}
    >
      <QueryState
        query={appts}
        what="Appointments"
        isEmpty={(a) => a.length === 0}
        empty={
          <EmptyState
            icon={<CalendarDays className="size-5" />}
            title="No appointments"
            description="Appointments booked for this patient appear here."
            action={canBook ? <Button variant="secondary" onClick={onBook}>Book an appointment</Button> : undefined}
          />
        }
      >
        {(a) => (
          <ul className="divide-y divide-line">
            {a.map((appt) => (
              <li key={appt.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="font-medium">
                    {formatDateTime(appt.starts_at)}
                    {!isInPast(appt.starts_at) && <Badge tone="info">Upcoming</Badge>}
                  </p>
                  <p className="truncate text-sm text-muted">
                    {humanize(appt.mode)} · {appt.doctor_name ?? "Doctor"}
                    {appt.reason ? ` · ${appt.reason}` : ""}
                  </p>
                </div>
                <StatusBadge status={appt.status} />
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Card>
  );
}

// --- Follow-ups ----------------------------------------------------------------------------------

export function FollowUpsSection({ patientId, canManage, onNew }: { patientId: string; canManage: boolean; onNew: () => void }) {
  const followUps = useFollowUps(patientId);
  const close = useCloseFollowUp(patientId);
  const toast = useToast();
  const today = new Date().toISOString().slice(0, 10);

  const act = async (id: string, outcome: "complete" | "cancel") => {
    try {
      await close.mutateAsync({ follow_up_id: id, outcome });
      toast.success(outcome === "complete" ? "Follow-up marked done" : "Follow-up cancelled");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <Card
      bodyClassName="p-0"
      title="Follow-ups"
      action={canManage && <Button size="sm" variant="secondary" onClick={onNew}>Set follow-up</Button>}
    >
      <QueryState
        query={followUps}
        what="Follow-ups"
        isEmpty={(f) => f.length === 0}
        empty={
          <EmptyState
            icon={<CalendarClock className="size-5" />}
            title="No follow-ups"
            description="Set a follow-up to be reminded when this patient should be reviewed."
            action={canManage ? <Button variant="secondary" onClick={onNew}>Set a follow-up</Button> : undefined}
          />
        }
      >
        {(f) => (
          <ul className="divide-y divide-line">
            {f.map((item) => {
              const open = item.status === "open" || item.status === "booked";
              return (
                <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <p className="font-medium">
                      Due {formatDate(item.due_date)}{" "}
                      {open && item.due_date < today && <Badge tone="danger">Overdue</Badge>}
                    </p>
                    <p className="truncate text-sm text-muted">
                      {item.reason ?? "No reason recorded"} · {item.doctor_name ?? "Doctor"}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusBadge status={item.status} />
                    {canManage && open && (
                      <>
                        <Button size="sm" variant="secondary" onClick={() => void act(item.id, "complete")}>
                          Mark done
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => void act(item.id, "cancel")}>
                          Cancel
                        </Button>
                      </>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </QueryState>
    </Card>
  );
}
