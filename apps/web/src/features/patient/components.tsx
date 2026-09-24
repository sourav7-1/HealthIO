import { AlarmClock, Check, CircleSlash, Clock, Plus, Stethoscope, Trash2, UserRound, X } from "lucide-react";
import { useState } from "react";

import { Alert, Badge, Button, Dialog, Field, Input, Textarea, cn, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { formatTime, humanize, isInPast } from "@/lib/format";

import { useDoseAction, type Dose } from "./api";
import { useMode } from "./context";

/**
 * Doctor prescribed vs patient self-reported, always shown in words and an icon
 * (never colour alone).
 */
export function SourceBadge({ source }: { source: string }) {
  const mode = useMode();
  const self = mode === "self";
  if (source === "prescription") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-info/12 px-2.5 py-1 text-xs font-semibold text-info">
        <Stethoscope className="size-3.5" aria-hidden /> {self ? "Prescribed by your doctor" : "Prescribed by a doctor"}
      </span>
    );
  }
  if (source === "clinician_recorded") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-info/12 px-2.5 py-1 text-xs font-semibold text-info">
        <Stethoscope className="size-3.5" aria-hidden /> {self ? "Recorded by your doctor" : "Recorded by a doctor"}
      </span>
    );
  }
  if (source === "self_reported" || source === "patient" || source === "caregiver") {
    // Self-reported medicines do not record who added them; allergies and documents do.
    const label =
      source === "caregiver"
        ? self
          ? "Added by your caregiver"
          : "Added by a caregiver"
        : source === "patient"
          ? self
            ? "Added by you"
            : "Added by the patient"
          : self
            ? "Added by you or your caregiver"
            : "Added by patient or family";
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1 text-xs font-semibold text-muted">
        <UserRound className="size-3.5" aria-hidden /> {label}
      </span>
    );
  }
  if (source === "doctor") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-info/12 px-2.5 py-1 text-xs font-semibold text-info">
        <Stethoscope className="size-3.5" aria-hidden /> {self ? "From your doctor" : "From a doctor"}
      </span>
    );
  }
  return <Badge>{humanize(source)}</Badge>;
}

// --- Dose card ----------------------------------------------------------------------------------

const DOSE_STATE: Record<string, { label: string; tone: string; icon: React.ReactNode }> = {
  taken: { label: "Taken", tone: "text-success", icon: <Check className="size-4" aria-hidden /> },
  skipped: { label: "Skipped", tone: "text-muted", icon: <CircleSlash className="size-4" aria-hidden /> },
  missed: { label: "Missed", tone: "text-danger", icon: <X className="size-4" aria-hidden /> },
  snoozed: { label: "Snoozed", tone: "text-warning", icon: <AlarmClock className="size-4" aria-hidden /> },
  scheduled: { label: "To take", tone: "text-fg", icon: <Clock className="size-4" aria-hidden /> },
};

export function DoseCard({ dose, patientId, canAct = true }: { dose: Dose; patientId: string; canAct?: boolean }) {
  const action = useDoseAction(patientId);
  const toast = useToast();
  const [skipping, setSkipping] = useState(false);
  const [reason, setReason] = useState("");
  const state = DOSE_STATE[dose.status] ?? DOSE_STATE.scheduled!;
  const open = dose.status === "scheduled" || dose.status === "snoozed";
  const overdue = open && dose.scheduled_at !== null && isInPast(dose.scheduled_at);

  const run = async (kind: "take" | "skip" | "snooze", extra: { reason?: string } = {}) => {
    try {
      await action.mutateAsync({ dose_id: dose.id, action: kind, ...extra });
      toast.success(kind === "take" ? "Marked as taken" : kind === "skip" ? "Marked as skipped" : "Reminder snoozed");
      setSkipping(false);
      setReason("");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <article
      className={cn(
        "rounded-xl border bg-surface p-4",
        overdue ? "border-warning" : "border-line",
        !open && "opacity-80",
      )}
      aria-label={`${dose.medication_name} at ${formatTime(dose.scheduled_at ?? dose.taken_at)}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-lg font-semibold tabular-nums">{formatTime(dose.scheduled_at ?? dose.taken_at)}</p>
          <p className="text-base font-medium">
            {dose.medication_name}
            {dose.strength && <span className="font-normal text-muted"> {dose.strength}</span>}
          </p>
          {(dose.meal_relation || dose.instructions) && (
            <p className="mt-0.5 text-sm text-muted">
              {[dose.meal_relation && dose.meal_relation !== "any" ? humanize(dose.meal_relation) : null, dose.instructions]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
          <div className="mt-2">
            <SourceBadge source={dose.source} />
          </div>
        </div>
        <p className={cn("flex items-center gap-1 text-sm font-semibold", state.tone)}>
          {state.icon}
          {overdue ? "Due now" : state.label}
          {dose.status === "snoozed" && dose.snoozed_until && ` until ${formatTime(dose.snoozed_until)}`}
          {dose.status === "taken" && dose.taken_at && ` at ${formatTime(dose.taken_at)}`}
        </p>
      </div>

      {canAct && (open || dose.status === "missed") && (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button onClick={() => void run("take")} loading={action.isPending} icon={<Check className="size-4" />} className="min-w-28">
            {dose.status === "missed" ? "I took it" : "Taken"}
          </Button>
          <Button variant="secondary" onClick={() => setSkipping(true)} icon={<CircleSlash className="size-4" />}>
            Skip
          </Button>
          {open && dose.snooze_count < 3 && (
            <Button variant="ghost" onClick={() => void run("snooze")} icon={<AlarmClock className="size-4" />}>
              Snooze
            </Button>
          )}
        </div>
      )}

      <Dialog
        open={skipping}
        onClose={() => setSkipping(false)}
        title={`Skip ${dose.medication_name}?`}
        description="Skipping is recorded so you and your doctor can see it. If you are unsure whether to skip a prescribed medicine, ask your doctor or pharmacist."
        footer={
          <>
            <Button variant="secondary" onClick={() => setSkipping(false)}>
              Back
            </Button>
            <Button onClick={() => void run("skip", { reason: reason.trim() || undefined })} loading={action.isPending}>
              Mark as skipped
            </Button>
          </>
        }
      >
        <Field label="Reason (optional)">
          {(p) => <Textarea {...p} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
        </Field>
      </Dialog>
    </article>
  );
}

// --- Reminder times picker ------------------------------------------------------------------------

const PRESETS: { label: string; time: string }[] = [
  { label: "Morning", time: "08:00" },
  { label: "Afternoon", time: "14:00" },
  { label: "Evening", time: "20:00" },
  { label: "Bedtime", time: "22:00" },
];

export function TimesPicker({ value, onChange, error }: { value: string[]; onChange: (v: string[]) => void; error?: string }) {
  const add = (t: string) => {
    if (!value.includes(t) && value.length < 8) onChange([...value, t].sort());
  };
  return (
    <fieldset>
      <legend className="text-sm font-medium">Remind me at</legend>
      <div className="mt-2 flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <button
            key={p.time}
            type="button"
            onClick={() => add(p.time)}
            disabled={value.includes(p.time)}
            className="min-h-11 rounded-full border border-line px-4 text-sm hover:bg-surface-2 disabled:opacity-40"
          >
            + {p.label} ({p.time})
          </button>
        ))}
      </div>
      <ul className="mt-3 flex flex-col gap-2">
        {value.map((t, i) => (
          <li key={`${t}-${i}`} className="flex items-center gap-2">
            <Input
              type="time"
              value={t}
              aria-label={`Reminder time ${i + 1}`}
              onChange={(e) => onChange(value.map((v, j) => (j === i ? e.target.value : v)).sort())}
              className="max-w-40"
            />
            <Button variant="ghost" size="sm" aria-label={`Remove ${t}`} onClick={() => onChange(value.filter((_, j) => j !== i))}>
              <Trash2 className="size-4" />
            </Button>
          </li>
        ))}
      </ul>
      {value.length < 8 && (
        <Button variant="ghost" size="sm" className="mt-2" icon={<Plus className="size-4" />} onClick={() => add("12:00")}>
          Add another time
        </Button>
      )}
      {error && (
        <p className="mt-2 text-sm text-danger" role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}

export function SafetyNote() {
  return (
    <Alert tone="info">
      Do not stop or change a medicine your doctor prescribed without talking to them first.
    </Alert>
  );
}
