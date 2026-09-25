import {
  CalendarClock,
  CalendarDays,
  History,
  NotebookPen,
  Stethoscope,
  Thermometer,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { Badge, Button, Card, EmptyState, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { formatDate, formatDateTime, humanize, isInPast } from "@/lib/format";

import {
  useAppointments,
  useCloseFollowUp,
  useFollowUps,
  useMedicalHistory,
  useVisits,
  type Overview,
} from "@/features/chart/api";
import { doctorLink } from "@/features/timeline/links";
import { useRecentActivity } from "@/features/timeline/api";
import { KINDS } from "@/features/timeline/kinds";
import { AddSymptomDialog } from "@/features/timeline/SymptomDialogs";
import { TimelineView } from "@/features/timeline/TimelineView";
import { DiagnosisDialog } from "../forms/visit";
import { DefinitionList, QueryState, SourceLabel, StatusBadge } from "@/features/chart/shared";

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
  const recent = useRecentActivity(patientId);
  return (
    <QueryState
      query={recent}
      what="Activity"
      isEmpty={(t) => t.items.length === 0}
      empty={
        <EmptyState
          icon={<History className="size-5" />}
          title="Nothing recorded yet"
          description="Visits, prescriptions, tests and appointments will appear here as they are recorded."
        />
      }
    >
      {(t) => (
        <ol className="divide-y divide-line">
          {t.items.map((e) => {
            const meta = KINDS[e.kind];
            const to = doctorLink(e, patientId);
            return (
              <li key={e.key} className="flex items-start justify-between gap-3 py-2.5">
                <div className="flex min-w-0 gap-3">
                  <span className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-full ${meta.tone}`} aria-hidden>
                    <meta.icon className="size-3.5" />
                  </span>
                  <div className="min-w-0">
                    <p className="font-medium">{to ? <Link to={to} className="hover:underline">{e.title}</Link> : e.title}</p>
                    {e.detail && <p className="line-clamp-1 text-sm text-muted">{e.detail}</p>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={e.status} />
                  <time dateTime={e.at} className="text-xs text-muted tabular-nums">
                    {e.date_only ? formatDate(e.at.slice(0, 10)) : formatDateTime(e.at)}
                  </time>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </QueryState>
  );
}

// --- Timeline ----------------------------------------------------------------------------------

export function TimelineSection({ patientId, overview }: { patientId: string; overview: Overview }) {
  const [recording, setRecording] = useState(false);
  const canEdit = overview.permissions.includes("edit_clinical_records");
  return (
    <>
      <TimelineView
        patientId={patientId}
        variant="doctor"
        linkFor={(e) => doctorLink(e, patientId)}
        correctable={canEdit ? ["doctor"] : []}
        actions={
          canEdit && (
            <Button size="sm" variant="secondary" icon={<Thermometer className="size-4" />} onClick={() => setRecording(true)}>
              Record a symptom
            </Button>
          )
        }
      />
      {recording && <AddSymptomDialog patientId={patientId} as="document" onClose={() => setRecording(false)} />}
    </>
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
