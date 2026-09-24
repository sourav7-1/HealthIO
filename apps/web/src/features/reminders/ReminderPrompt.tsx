/**
 * "Time for your medication": the in-app reminder. It appears for any dose that is due
 * (polled from the API, so it works without Web Push) and when a push notification is
 * tapped (`?reminder=<doseId>&action=take|snooze|skip`). The dose shown is exactly what the
 * medicine record says; the prompt only records what the person did.
 */
import { AlarmClock, BellRing, Check, CircleSlash } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { Button, Dialog, Field, Textarea, useToast } from "@/components/ui";
import { OriginBadge } from "@/features/meds/labels";
import { useDoseAction } from "@/features/patient/api";
import { useActivePatient } from "@/features/patient/context";
import { errorMessage } from "@/lib/api";
import { formatTime, humanize } from "@/lib/format";

import { REMINDER_TITLE, useDueReminders, type Reminder } from "./api";

type Origin = Parameters<typeof OriginBadge>[0]["origin"];

export function ReminderPrompt() {
  const { patientId, can, mode } = useActivePatient();
  const allowed = mode === "self" || can("log_doses");
  const due = useDueReminders(patientId, allowed);
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());

  // A tapped notification whose app window was already open: the service worker posts
  // the URL instead of opening a second window.
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const onMessage = (e: MessageEvent<{ type?: string; url?: string }>) => {
      if (e.data?.type === "open-reminder" && e.data.url?.startsWith("/")) void navigate(e.data.url);
    };
    navigator.serviceWorker.addEventListener("message", onMessage);
    return () => navigator.serviceWorker.removeEventListener("message", onMessage);
  }, [navigate]);

  const requested = params.get("reminder");
  const requestedAction = params.get("action");
  const queue = useMemo(() => {
    const list = (due.data ?? []).filter((r) => r.dose_id === requested || !dismissed.has(key(r)));
    // The dose from a tapped notification goes first.
    return list.sort((a, b) => Number(b.dose_id === requested) - Number(a.dose_id === requested));
  }, [due.data, dismissed, requested]);
  const current = queue[0];

  const clearUrl = () => {
    if (!params.has("reminder") && !params.has("action")) return;
    const next = new URLSearchParams(params);
    next.delete("reminder");
    next.delete("action");
    setParams(next, { replace: true });
  };

  if (!allowed || !current) return null;
  const close = () => {
    setDismissed((s) => new Set(s).add(key(current)));
    clearUrl();
  };
  return (
    <ReminderDialog
      key={key(current)}
      reminder={current}
      remaining={queue.length - 1}
      patientId={patientId}
      initialAction={current.dose_id === requested ? requestedAction : null}
      onDone={clearUrl}
      onClose={close}
    />
  );
}

// A new reminder for the same dose (after a snooze) shows again even if the last was closed.
const key = (r: Reminder) => `${r.dose_id}:${r.status}:${r.snooze_count}`;

export function ReminderDialog({
  reminder: r,
  remaining,
  patientId,
  initialAction,
  onDone,
  onClose,
}: {
  reminder: Reminder;
  remaining: number;
  patientId: string;
  initialAction: string | null;
  onDone: () => void;
  onClose: () => void;
}) {
  const action = useDoseAction(patientId);
  const toast = useToast();
  const [skipping, setSkipping] = useState(initialAction === "skip");
  const [reason, setReason] = useState("");
  const minutes = r.default_snooze_minutes as 5 | 10 | 15 | 30 | 60;

  const run = async (kind: "take" | "skip" | "snooze") => {
    try {
      await action.mutateAsync({
        dose_id: r.dose_id,
        action: kind,
        reason: kind === "skip" ? reason.trim() || undefined : undefined,
        minutes: kind === "snooze" ? minutes : undefined,
      });
      toast.success(kind === "take" ? "Marked as taken" : kind === "skip" ? "Marked as skipped" : `Reminder snoozed for ${minutes} minutes`);
      onDone();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const details = [r.meal && r.meal !== "any" ? humanize(r.meal) : null, r.instructions].filter(Boolean).join(" · ");

  return (
    <Dialog
      open
      onClose={onClose}
      title={REMINDER_TITLE}
      description={`Due at ${formatTime(r.scheduled_at)}${remaining > 0 ? ` · ${remaining} more after this` : ""}`}
      footer={
        skipping ? (
          <>
            <Button variant="secondary" onClick={() => setSkipping(false)}>
              Back
            </Button>
            <Button onClick={() => void run("skip")} loading={action.isPending}>
              Mark as skipped
            </Button>
          </>
        ) : (
          <>
            {r.can_snooze && (
              <Button variant="ghost" onClick={() => void run("snooze")} disabled={action.isPending} icon={<AlarmClock className="size-4" />}>
                Snooze {minutes} min
              </Button>
            )}
            <Button variant="secondary" onClick={() => setSkipping(true)} disabled={action.isPending} icon={<CircleSlash className="size-4" />}>
              Skip
            </Button>
            <Button
              onClick={() => void run("take")}
              loading={action.isPending}
              icon={<Check className="size-4" />}
              className="min-w-28"
              autoFocus={initialAction === "take" || initialAction === null}
            >
              Taken
            </Button>
          </>
        )
      }
    >
      <div className="flex gap-3">
        <span className="mt-1 grid size-10 shrink-0 place-items-center rounded-full bg-accent/12 text-accent" aria-hidden>
          <BellRing className="size-5" />
        </span>
        <dl className="flex min-w-0 flex-col gap-2">
          <div>
            <dt className="sr-only">Medicine</dt>
            <dd className="text-lg font-semibold">{r.medicine}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-muted">Dose</dt>
            <dd className="text-base">{r.dose ?? "Not recorded: check the label or ask your pharmacist"}</dd>
          </div>
          {details && (
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-muted">
                {r.instructions_verified ? "Instructions from the prescription" : "Instructions (not from a prescription)"}
              </dt>
              <dd className="text-sm">{details}</dd>
            </div>
          )}
          <div>
            <dt className="sr-only">Source</dt>
            <dd>
              <OriginBadge origin={r.origin as Origin} />
            </dd>
          </div>
        </dl>
      </div>
      {skipping && (
        <div className="mt-4 flex flex-col gap-2">
          <p className="text-sm text-muted">
            Skipping is recorded so you and your doctor can see it. If you are unsure whether to skip a prescribed medicine, ask
            your doctor or pharmacist.
          </p>
          <Field label="Reason (optional)">
            {(p) => <Textarea {...p} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
          </Field>
        </div>
      )}
      {r.snooze_count > 0 && !skipping && (
        <p className="mt-3 text-xs text-muted">
          Snoozed {r.snooze_count} time{r.snooze_count > 1 ? "s" : ""}
          {!r.can_snooze && " · no more snoozes for this dose"}
        </p>
      )}
    </Dialog>
  );
}
