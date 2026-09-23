import { CalendarClock, CalendarDays, ClipboardCheck, Pill, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Badge, Button, Card, EmptyState, ErrorState, Skeleton, Stat } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { formatDate, formatDateTime, formatTime, humanize } from "@/lib/format";

import { useDashboard } from "./api";
import { AddPatientDialog } from "./forms/AddPatientDialog";
import { PatientName, StatusBadge } from "./shared";

export function DashboardPage() {
  const dashboard = useDashboard();
  const [adding, setAdding] = useState(false);

  if (dashboard.isPending) return <DashboardSkeleton />;
  if (dashboard.isError) {
    return <ErrorState message={errorMessage(dashboard.error)} onRetry={() => void dashboard.refetch()} />;
  }
  const d = dashboard.data;
  const verified = d.verification_status === "verified";

  return (
    <>
      <PageHeader
        title={d.doctor_name ? `Good day, ${d.doctor_name}` : "Dashboard"}
        description={formatDate(d.today)}
        actions={
          verified && (
            <Button icon={<UserPlus className="size-4" />} onClick={() => setAdding(true)}>
              Add patient
            </Button>
          )
        }
      />

      {!verified && (
        <div className="mb-6">
          <Alert tone="warning" title="Your profile is awaiting verification">
            {d.verification_status === "unverified"
              ? "Add your medical council registration details so an administrator can verify you. "
              : "An administrator is checking your registration details. "}
            You can add patients and record care once you are verified.
          </Alert>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Total patients" value={d.total_patients} icon={<Users className="size-4" aria-hidden />} />
        <Stat
          label="Today's appointments"
          value={d.today_appointments.length}
          icon={<CalendarDays className="size-4" aria-hidden />}
        />
        <Stat
          label="Follow-ups (14 days)"
          value={d.upcoming_follow_ups.length}
          icon={<CalendarClock className="size-4" aria-hidden />}
        />
        <Stat
          label="Active treatments"
          value={d.active_treatments}
          hint="Medicines from prescriptions you issued"
          icon={<Pill className="size-4" aria-hidden />}
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-5">
        <Card title="Today's appointments" className="lg:col-span-3" bodyClassName="p-0">
          {d.today_appointments.length === 0 ? (
            <EmptyState
              icon={<CalendarDays className="size-5" />}
              title="No appointments today"
              description="Appointments you book from a patient's chart appear here on the day."
            />
          ) : (
            <ul className="divide-y divide-line">
              {d.today_appointments.map((a) => (
                <li key={a.id}>
                  <Link
                    to={`/doctor/patients/${a.patient_id}`}
                    className="flex items-center gap-4 px-4 py-3 hover:bg-surface-2"
                  >
                    <div className="w-16 shrink-0 text-sm font-semibold tabular-nums">{formatTime(a.starts_at)}</div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">
                        <PatientName name={a.patient_name} />
                      </p>
                      <p className="truncate text-sm text-muted">
                        {humanize(a.mode)}
                        {a.reason ? ` · ${a.reason}` : ""}
                      </p>
                    </div>
                    <StatusBadge status={a.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Upcoming follow-ups" className="lg:col-span-2" bodyClassName="p-0">
          {d.upcoming_follow_ups.length === 0 ? (
            <EmptyState
              icon={<ClipboardCheck className="size-5" />}
              title="No follow-ups due"
              description="Follow-ups you set during a visit appear here from two weeks before they are due."
            />
          ) : (
            <ul className="divide-y divide-line">
              {d.upcoming_follow_ups.map((f) => (
                <li key={f.id}>
                  <Link
                    to={`/doctor/patients/${f.patient_id}/follow-ups`}
                    className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-surface-2"
                  >
                    <div className="min-w-0">
                      <p className="truncate font-medium">
                        <PatientName name={f.patient_name} />
                      </p>
                      <p className="truncate text-sm text-muted">{f.reason ?? "No reason recorded"}</p>
                    </div>
                    <div className="shrink-0 text-right text-sm">
                      <p className="tabular-nums">{formatDate(f.due_date)}</p>
                      {f.overdue && <Badge tone="danger">Overdue</Badge>}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="Recently seen"
          className="lg:col-span-5"
          action={
            <Link to="/doctor/patients" className="text-sm font-medium text-accent hover:underline">
              All patients
            </Link>
          }
          bodyClassName="p-0"
        >
          {d.recent_patients.length === 0 ? (
            <EmptyState
              icon={<Users className="size-5" />}
              title="No visits recorded yet"
              description={
                d.total_patients === 0
                  ? "Add a patient you are seeing, or ask an existing patient to connect with you."
                  : "Patients you record visits for will appear here."
              }
              action={
                verified && d.total_patients === 0 ? (
                  <Button variant="secondary" onClick={() => setAdding(true)}>
                    Add your first patient
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <ul className="grid divide-y divide-line sm:grid-cols-2 sm:divide-y-0">
              {d.recent_patients.map((p) => (
                <li key={p.patient_id}>
                  <Link
                    to={`/doctor/patients/${p.patient_id}`}
                    className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-surface-2"
                  >
                    <span className="truncate font-medium">
                      <PatientName name={p.patient_name} />
                    </span>
                    <span className="shrink-0 text-sm text-muted">Last visit {formatDateTime(p.last_visit_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <AddPatientDialog open={adding} onClose={() => setAdding(false)} />
    </>
  );
}

function DashboardSkeleton() {
  return (
    <div role="status" aria-label="Loading dashboard">
      <Skeleton className="mb-6 h-8 w-64" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-28 rounded-xl" />
        ))}
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-5">
        <Skeleton className="h-64 rounded-xl lg:col-span-3" />
        <Skeleton className="h-64 rounded-xl lg:col-span-2" />
      </div>
    </div>
  );
}
