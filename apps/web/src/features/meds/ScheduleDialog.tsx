/**
 * Change a medicine's schedule. The change is checked first:
 * - nothing to confirm → save;
 * - warnings (e.g. doses close together) → the person acknowledges them;
 * - clinically relevant for a prescribed medicine (dose, how often, days, food) → ask the
 *   doctor in the app, or record which doctor or pharmacist advised it.
 * The prescription itself is never changed.
 */
import { AlertTriangle, Stethoscope } from "lucide-react";
import { useState } from "react";

import { Alert, Button, Checkbox, Dialog, Field, Input, Select, Textarea, useToast } from "@/components/ui";
import { TimesPicker } from "@/features/patient/components";
import { useActivePatient } from "@/features/patient/context";
import { errorMessage, type Schemas } from "@/lib/api";

import { checkSchedule, useChangeSchedule, useRequestChange, type Check, type Med, type ScheduleIn } from "./api";
import { isPrescribed } from "./labels";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function initial(med: Med): ScheduleIn {
  const s = med.schedule;
  return {
    schedule_type: s?.type ?? (med.is_prn ? "as_needed" : "fixed_times"),
    times_of_day: (s?.times_of_day ?? ["08:00"]).map((t) => t.slice(0, 5)),
    interval_hours: s?.interval_minutes ? s.interval_minutes / 60 : null,
    pattern: { kind: s?.pattern.kind ?? "daily", every: s?.pattern.every ?? 1, weekdays: s?.pattern.weekdays ?? [] },
    dose_amount: s?.dose_amount ?? null,
    dose_unit: s?.dose_unit ?? null,
    meal_relation: s?.meal_relation ?? null,
    start_date: med.start_date,
    end_date: med.end_date,
    clear_end_date: false,
    timezone: null,
  };
}

export function ScheduleDialog({ med, onClose }: { med: Med; onClose: () => void }) {
  const { patientId, mode } = useActivePatient();
  const save = useChangeSchedule(patientId, med.id);
  const ask = useRequestChange(patientId, med.id);
  const toast = useToast();
  const [form, setForm] = useState<ScheduleIn>(() => initial(med));
  const [check, setCheck] = useState<Check | null>(null);
  const [ack, setAck] = useState(false);
  const [path, setPath] = useState<"advice" | "ask" | null>(null);
  const [advice, setAdvice] = useState<Schemas["AdviceIn"]>({ role: "doctor", name: "" });
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const prescribed = isPrescribed(med.origin);
  const self = mode === "self";

  const set = <K extends keyof ScheduleIn>(k: K, v: ScheduleIn[K]) => {
    setForm((f) => ({ ...f, [k]: v }));
    setCheck(null);
    setAck(false);
    setPath(null);
  };

  const body = (): ScheduleIn => ({
    ...form,
    times_of_day: form.schedule_type === "as_needed" ? [] : form.schedule_type === "interval" ? (form.times_of_day ?? []).slice(0, 1) : (form.times_of_day ?? []),
    interval_hours: form.schedule_type === "interval" ? form.interval_hours : null,
    dose_unit: form.dose_unit?.trim() || null,
    dose_amount: form.dose_amount ? String(form.dose_amount) : null,
  });

  const review = async () => {
    setError(null);
    setBusy(true);
    try {
      setCheck(await checkSchedule(patientId, med.id, body()));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    setError(null);
    try {
      await save.mutateAsync({
        ...body(),
        acknowledged: ack || check?.requires === "none",
        advice: check?.requires === "clinician" && path === "advice" ? advice : null,
        reason: reason.trim() || null,
      });
      toast.success("Schedule updated");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const request = async () => {
    setError(null);
    try {
      await ask.mutateAsync({ kind: "schedule", schedule: body(), message: reason.trim() || null });
      toast.success("Sent to the doctor. Nothing changes until they confirm.");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const canSave =
    check !== null &&
    (check.requires === "none" ||
      (check.requires === "acknowledgement" && ack) ||
      (check.requires === "clinician" && path === "advice" && ack && advice.name.trim().length >= 2));

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title={`Change the schedule: ${med.name}`}
      description={
        prescribed
          ? "You can move reminder times. Changing the dose, how often, which days or food instructions needs your doctor or pharmacist."
          : "This is a medicine you added, so you can change it."
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          {check === null ? (
            <Button onClick={() => void review()} loading={busy}>Review change</Button>
          ) : check.requires === "clinician" && path === "ask" ? (
            <Button onClick={() => void request()} loading={ask.isPending}>Send to the doctor</Button>
          ) : (
            <Button onClick={() => void apply()} disabled={!canSave} loading={save.isPending}>Save schedule</Button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <Field label="How it is taken">
          {(p) => (
            <Select {...p} value={form.schedule_type} onChange={(e) => set("schedule_type", e.target.value as ScheduleIn["schedule_type"])}>
              <option value="fixed_times">At set times of day</option>
              <option value="interval">Every few hours</option>
              <option value="as_needed">Only when needed</option>
            </Select>
          )}
        </Field>

        {form.schedule_type === "fixed_times" && (
          <>
            <TimesPicker value={form.times_of_day ?? []} onChange={(v) => set("times_of_day", v)} />
            <Field label="Which days">
              {(p) => (
                <Select
                  {...p}
                  value={form.pattern?.kind ?? "daily"}
                  onChange={(e) => {
                    const kind = e.target.value as "daily" | "every_n_days" | "weekdays";
                    set("pattern", { kind, every: kind === "every_n_days" ? Math.max(2, form.pattern?.every ?? 2) : 1, weekdays: form.pattern?.weekdays ?? [] });
                  }}
                >
                  <option value="daily">Every day</option>
                  <option value="every_n_days">Every few days</option>
                  <option value="weekdays">On chosen days of the week</option>
                </Select>
              )}
            </Field>
            {form.pattern?.kind === "every_n_days" && (
              <Field label="Every how many days">
                {(p) => (
                  <Input {...p} type="number" min={2} max={30} value={form.pattern?.every ?? 2}
                    onChange={(e) => set("pattern", { ...form.pattern!, every: Number(e.target.value) || 2 })} />
                )}
              </Field>
            )}
            {form.pattern?.kind === "weekdays" && (
              <fieldset className="flex flex-wrap gap-3">
                <legend className="mb-2 text-sm font-medium">Days</legend>
                {WEEKDAYS.map((d, i) => (
                  <Checkbox
                    key={d}
                    label={d}
                    checked={form.pattern?.weekdays?.includes(i + 1) ?? false}
                    onChange={(e) => {
                      const cur = form.pattern?.weekdays ?? [];
                      set("pattern", { ...form.pattern!, weekdays: e.target.checked ? [...cur, i + 1] : cur.filter((x) => x !== i + 1) });
                    }}
                  />
                ))}
              </fieldset>
            )}
          </>
        )}

        {form.schedule_type === "interval" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Every (hours)">
              {(p) => <Input {...p} type="number" min={1} max={48} step={0.5} value={form.interval_hours ?? 8} onChange={(e) => set("interval_hours", Number(e.target.value) || null)} />}
            </Field>
            <Field label="First dose at">
              {(p) => <Input {...p} type="time" value={form.times_of_day?.[0] ?? "06:00"} onChange={(e) => set("times_of_day", [e.target.value])} />}
            </Field>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Dose">
            {(p) => <Input {...p} inputMode="decimal" placeholder="e.g. 1" value={form.dose_amount ?? ""} onChange={(e) => set("dose_amount", e.target.value || null)} />}
          </Field>
          <Field label="Unit">
            {(p) => <Input {...p} placeholder="tablet, ml…" value={form.dose_unit ?? ""} onChange={(e) => set("dose_unit", e.target.value || null)} />}
          </Field>
          <Field label="With food">
            {(p) => (
              <Select {...p} value={form.meal_relation ?? ""} onChange={(e) => set("meal_relation", (e.target.value || null) as ScheduleIn["meal_relation"])}>
                <option value="">No instruction</option>
                <option value="before_food">Before food</option>
                <option value="after_food">After food</option>
                <option value="with_food">With food</option>
                <option value="empty_stomach">Empty stomach</option>
                <option value="bedtime">At bedtime</option>
              </Select>
            )}
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Start date">
            {(p) => <Input {...p} type="date" value={form.start_date ?? ""} onChange={(e) => set("start_date", e.target.value || null)} />}
          </Field>
          <Field label="End date" hint={form.clear_end_date ? "No end date" : undefined}>
            {(p) => <Input {...p} type="date" value={form.clear_end_date ? "" : form.end_date ?? ""} onChange={(e) => { set("end_date", e.target.value || null); set("clear_end_date", false); }} />}
          </Field>
        </div>
        {!prescribed && (
          <Checkbox label="No end date" checked={form.clear_end_date ?? false} onChange={(e) => set("clear_end_date", e.target.checked)} />
        )}
        <Field label="Why are you changing it? (optional)">
          {(p) => <Textarea {...p} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
        </Field>

        {check && check.findings.length > 0 && (
          <Alert tone={check.requires === "clinician" ? "danger" : "warning"} title={check.requires === "clinician" ? "This changes what was prescribed" : "Please check"}>
            <ul className="list-inside list-disc">
              {check.findings.map((f) => <li key={f.code}>{f.message}</li>)}
            </ul>
          </Alert>
        )}
        {check?.requires === "none" && <Alert tone="success">This only changes {self ? "your" : "the"} reminders. Nothing else needs to be confirmed.</Alert>}
        {check && check.requires !== "none" && (
          <Checkbox
            label={check.requires === "clinician" ? "I understand this differs from the prescription" : "I have read these warnings"}
            checked={ack}
            onChange={(e) => setAck(e.target.checked)}
          />
        )}
        {check?.requires === "clinician" && (
          <fieldset className="flex flex-col gap-3 rounded-xl border border-line p-4">
            <legend className="px-1 text-sm font-medium">Who confirms this change?</legend>
            <label className="flex items-start gap-3 text-sm">
              <input type="radio" name="path" className="mt-1 size-4" checked={path === "ask"} onChange={() => setPath("ask")} />
              <span><Stethoscope className="mr-1 inline size-4" aria-hidden /> Ask {self ? "my" : "their"} doctor in the app. Nothing changes until the doctor confirms.</span>
            </label>
            <label className="flex items-start gap-3 text-sm">
              <input type="radio" name="path" className="mt-1 size-4" checked={path === "advice"} onChange={() => setPath("advice")} />
              <span>A doctor or pharmacist already told {self ? "me" : "them"} to make this change.</span>
            </label>
            {path === "advice" && (
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Who advised it">
                  {(p) => (
                    <Select {...p} value={advice.role} onChange={(e) => setAdvice((a) => ({ ...a, role: e.target.value as "doctor" | "pharmacist" }))}>
                      <option value="doctor">A doctor</option>
                      <option value="pharmacist">A pharmacist</option>
                    </Select>
                  )}
                </Field>
                <Field label="Their name" required>
                  {(p) => <Input {...p} value={advice.name} onChange={(e) => setAdvice((a) => ({ ...a, name: e.target.value }))} />}
                </Field>
                <p className="flex items-start gap-2 text-xs text-muted sm:col-span-2">
                  <AlertTriangle className="size-4 shrink-0" aria-hidden /> This is recorded in the medicine&apos;s history with their name, and the doctor can see it.
                </p>
              </div>
            )}
          </fieldset>
        )}
      </div>
    </Dialog>
  );
}
