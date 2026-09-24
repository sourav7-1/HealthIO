import { ArrowLeft, CalendarClock, CircleSlash, History, Pause, Pencil, Play } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Button, Card, useToast } from "@/components/ui";
import { DefinitionList, QueryState } from "@/features/chart/shared";
import { SafetyNote } from "@/features/patient/components";
import { useActivePatient } from "@/features/patient/context";
import { errorMessage } from "@/lib/api";
import { formatDate, formatDateTime, humanize } from "@/lib/format";

import { useMedication, useMedicationHistory, useResume, useWithdrawRequest, type Med, type MedEvent } from "./api";
import { OriginBadge, StatusPill, courseText, isPrescribed, scheduleText } from "./labels";
import { ScheduleDialog } from "./ScheduleDialog";
import { StopPauseDialog } from "./StopPauseDialog";

const EVENT_LABEL: Record<MedEvent["event_type"], string> = {
  created: "Added",
  confirmed: "Set up with reminders",
  schedule_changed: "Schedule changed",
  paused: "Paused",
  resumed: "Restarted",
  stopped: "Discontinued",
  completed: "Course completed",
  change_requested: "Change asked of the doctor",
  change_approved: "Doctor approved the change",
  change_declined: "Doctor declined the change",
  change_withdrawn: "Request withdrawn",
  duplicate_noted: "Looks like another medicine on the list",
};

function who(e: MedEvent): string {
  if (e.actor_role === "system") return "Automatically, from the course dates";
  return `${e.actor_name ?? "Someone"} (${humanize(e.actor_role).toLowerCase()})`;
}

function HistoryCard({ patientId, medId }: { patientId: string; medId: string }) {
  const history = useMedicationHistory(patientId, medId);
  return (
    <Card title={<span className="flex items-center gap-2"><History className="size-4" aria-hidden /> History</span>}>
      <QueryState query={history} what="History" isEmpty={(h) => h.length === 0} empty={<p className="text-sm text-muted">No history yet.</p>}>
        {(events) => (
          <ol className="relative flex flex-col gap-4 border-l border-line pl-4">
            {[...events].reverse().map((e) => (
              <li key={e.id} className="text-sm">
                <p className="font-medium">{EVENT_LABEL[e.event_type]}</p>
                <p className="text-muted">{formatDateTime(e.occurred_at)} · {who(e)}</p>
                {e.advised_by_name && (
                  <p>On the advice of {e.advised_by_role} {e.advised_by_name} (as reported)</p>
                )}
                {e.details?.own_decision === true && <p>Recorded as the patient&apos;s own decision</p>}
                {e.details?.confirmed_by === "doctor" && <p>Confirmed by the doctor</p>}
                {e.reason && <p className="text-muted">“{e.reason}”</p>}
              </li>
            ))}
          </ol>
        )}
      </QueryState>
    </Card>
  );
}

function Actions({ med, onDialog }: { med: Med; onDialog: (d: "schedule" | "stop" | "pause") => void }) {
  const { patientId, can } = useActivePatient();
  const resume = useResume(patientId, med.id);
  const toast = useToast();
  const manage = can("manage_reminders");
  const report = can("report_health_info");
  return (
    <div className="flex flex-wrap gap-2">
      {med.status === "active" && manage && (
        <Button icon={<Pencil className="size-4" />} onClick={() => onDialog("schedule")}>Change schedule</Button>
      )}
      {med.status === "active" && manage && (
        <Button variant="secondary" icon={<Pause className="size-4" />} onClick={() => onDialog("pause")}>Pause</Button>
      )}
      {med.status === "paused" && manage && (
        <Button
          icon={<Play className="size-4" />}
          loading={resume.isPending}
          onClick={() => void resume.mutateAsync().then(() => toast.success("Restarted"), (err) => toast.error(errorMessage(err)))}
        >
          Restart
        </Button>
      )}
      {["active", "paused", "pending_confirmation"].includes(med.status) && report && (
        <Button variant="ghost" icon={<CircleSlash className="size-4" />} onClick={() => onDialog("stop")}>Discontinue</Button>
      )}
    </div>
  );
}

export function MedicationDetailPage() {
  const { patientId, base, can } = useActivePatient();
  const { medicationId = "" } = useParams();
  const med = useMedication(patientId, medicationId);
  const withdraw = useWithdrawRequest(patientId);
  const toast = useToast();
  const [dialog, setDialog] = useState<"schedule" | "stop" | "pause" | null>(null);

  return (
    <>
      <PageHeader
        back={<Link to={`${base}/medications`} className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-fg"><ArrowLeft className="size-4" aria-hidden /> Medicines</Link>}
        title={med.data ? `${med.data.name}${med.data.strength ? ` ${med.data.strength}` : ""}` : "Medicine"}
      />
      <QueryState query={med} what="Medicine">
        {(m) => (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <div className="flex flex-col gap-6">
              <div className="flex flex-wrap items-center gap-2">
                <OriginBadge origin={m.origin} />
                <StatusPill status={m.status} />
              </div>
              {(m.duplicates ?? []).length > 0 && (
                <Alert tone="warning" title="This may be on the list twice">
                  <ul className="list-inside list-disc">
                    {(m.duplicates ?? []).map((d) => (
                      <li key={d.medication_id}>
                        <Link className="underline" to={`${base}/medications/${d.medication_id}`}>{d.name}</Link>{" "}
                        ({d.kind === "same_name" ? "same medicine" : "same generic medicine"})
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1">Taking both could mean taking too much. Check with the doctor or pharmacist.</p>
                </Alert>
              )}
              {m.status === "paused" && (
                <Alert tone="warning" title="Paused">
                  Since {formatDate(m.paused_at?.slice(0, 10))}{m.resume_on ? `, planned restart ${formatDate(m.resume_on)}` : ""}.
                  {m.pause_reason ? ` Reason: ${m.pause_reason}` : ""}
                </Alert>
              )}
              {m.pending_request && (
                <Alert tone="info" title="Waiting for the doctor">
                  Asked to {m.pending_request.kind === "schedule" ? "change the schedule" : m.pending_request.kind} on{" "}
                  {formatDateTime(m.pending_request.created_at)}. Nothing changes until the doctor confirms.
                  {can("manage_reminders") && (
                    <div className="mt-2">
                      <Button size="sm" variant="secondary" loading={withdraw.isPending}
                        onClick={() => void withdraw.mutateAsync(m.pending_request!.id).then(() => toast.success("Request withdrawn"), (err) => toast.error(errorMessage(err)))}>
                        Withdraw request
                      </Button>
                    </div>
                  )}
                </Alert>
              )}

              {m.prescribed && (
                <Card title="What the prescription says">
                  <DefinitionList
                    items={[
                      ["As written", m.prescribed_directions ?? "—"],
                      ["Times a day", m.prescribed.times_per_day ? String(m.prescribed.times_per_day) : m.prescribed.is_prn ? "When needed" : "Not stated"],
                      ["Dose", m.prescribed.dose_amount ? `${Number(m.prescribed.dose_amount)} ${m.prescribed.dose_unit ?? ""}` : "Not stated"],
                      ["Food", m.prescribed.meal_relation ? humanize(m.prescribed.meal_relation) : "Not stated"],
                      ["Course", m.prescribed.duration_days ? `${m.prescribed.duration_days} days` : "Not stated"],
                    ]}
                  />
                  <Link className="mt-3 inline-block text-sm font-medium text-accent underline" to={`${base}/prescriptions/${m.prescribed.prescription_id}`}>
                    Open the prescription
                  </Link>
                </Card>
              )}

              <Card title={<span className="flex items-center gap-2"><CalendarClock className="size-4" aria-hidden /> Schedule</span>}>
                <DefinitionList
                  items={[
                    ["When", m.status === "pending_confirmation" ? "Not set up yet" : scheduleText(m)],
                    ["Course", courseText(m) ?? "Not set"],
                    ["Instructions", m.instructions ?? "—"],
                    ...(m.stopped_at ? ([["Discontinued", `${formatDate(m.stopped_at.slice(0, 10))}${m.stop_reason ? ` · ${m.stop_reason}` : ""}`]] as [string, string][]) : []),
                  ]}
                />
                <div className="mt-4">
                  <Actions med={m} onDialog={setDialog} />
                </div>
                {isPrescribed(m.origin) && <div className="mt-4"><SafetyNote /></div>}
              </Card>
            </div>
            <HistoryCard patientId={patientId} medId={m.id} />
            {dialog === "schedule" && <ScheduleDialog med={m} onClose={() => setDialog(null)} />}
            {(dialog === "stop" || dialog === "pause") && <StopPauseDialog med={m} action={dialog} onClose={() => setDialog(null)} />}
          </div>
        )}
      </QueryState>
    </>
  );
}

