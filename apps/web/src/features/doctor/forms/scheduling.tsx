import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Alert, Button, Dialog, Field, Input, Select, Textarea } from "@/components/ui";
import { dueDateFrom, isInPast, todayIso } from "@/lib/format";

import { useBookAppointment, useSetFollowUp, type FollowUp } from "../api";
import { useSubmit } from "../useSubmit";

// --- Follow-up ---------------------------------------------------------------------------------

const followUpSchema = z.object({
  mode: z.enum(["in", "on"]),
  amount: z.string().trim().optional(),
  unit: z.enum(["days", "weeks", "months"]),
  due_date: z.string().optional(),
  reason: z.string().trim().max(500).optional(),
});
type FollowUpValues = z.infer<typeof followUpSchema>;

export function FollowUpDialog({
  patientId,
  visitId,
  open,
  onClose,
}: {
  patientId: string;
  visitId?: string;
  open: boolean;
  onClose: () => void;
}) {
  const set = useSetFollowUp(patientId);
  const { register, handleSubmit, reset, setError, watch, formState } = useForm<FollowUpValues>({
    resolver: zodResolver(followUpSchema),
    defaultValues: { mode: "in", amount: "2", unit: "weeks" },
  });
  const { run, formError, clearFormError } = useSubmit(setError);
  const mode = watch("mode");
  const close = () => {
    reset();
    clearFormError();
    onClose();
  };
  const onSubmit = handleSubmit(async (v) => {
    const amount = Number(v.amount);
    const due =
      v.mode === "on" ? v.due_date : Number.isInteger(amount) && amount > 0 ? dueDateFrom(amount, v.unit) : undefined;
    if (!due) {
      setError(v.mode === "on" ? "due_date" : "amount", { message: "Enter when the follow-up is due" });
      return;
    }
    const ok = await run(
      () => set.mutateAsync({ due_date: due, reason: v.reason || null, source_visit_id: visitId ?? null }),
      "Follow-up set",
    );
    if (ok) close();
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Set a follow-up"
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="follow-up" loading={formState.isSubmitting}>
            Set follow-up
          </Button>
        </>
      }
    >
      <form id="follow-up" onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
        {formError && <Alert tone="danger">{formError}</Alert>}
        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input type="radio" value="in" {...register("mode")} /> Review in…
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" value="on" {...register("mode")} /> On a date
          </label>
        </div>
        {mode === "in" ? (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Amount" error={formState.errors.amount?.message}>
              {(p) => <Input {...p} inputMode="numeric" {...register("amount")} />}
            </Field>
            <Field label="Unit">
              {(p) => (
                <Select {...p} {...register("unit")}>
                  <option value="days">Days</option>
                  <option value="weeks">Weeks</option>
                  <option value="months">Months</option>
                </Select>
              )}
            </Field>
          </div>
        ) : (
          <Field label="Due date" error={formState.errors.due_date?.message}>
            {(p) => <Input {...p} type="date" min={todayIso()} {...register("due_date")} />}
          </Field>
        )}
        <Field label="Reason" hint="e.g. review blood pressure readings">
          {(p) => <Textarea {...p} rows={2} {...register("reason")} />}
        </Field>
      </form>
    </Dialog>
  );
}

// --- Appointment ---------------------------------------------------------------------------------

const apptSchema = z.object({
  date: z.string().min(1, "Choose a date").refine((v) => v >= todayIso(), "Choose today or a later date"),
  time: z.string().min(1, "Choose a time"),
  duration_minutes: z.string(),
  mode: z.enum(["in_person", "teleconsult", "home_visit"]),
  reason: z.string().trim().max(500).optional(),
  location: z.string().trim().max(200).optional(),
  follow_up_id: z.string().optional(),
});
type ApptValues = z.infer<typeof apptSchema>;

export function AppointmentDialog({
  patientId,
  openFollowUps,
  open,
  onClose,
}: {
  patientId: string;
  openFollowUps: FollowUp[];
  open: boolean;
  onClose: () => void;
}) {
  const book = useBookAppointment(patientId);
  const { register, handleSubmit, reset, setError, formState } = useForm<ApptValues>({
    resolver: zodResolver(apptSchema),
    defaultValues: { duration_minutes: "15", mode: "in_person", date: todayIso(1), time: "10:00" },
  });
  const { run, formError, clearFormError } = useSubmit(setError);
  const close = () => {
    reset();
    clearFormError();
    onClose();
  };
  const onSubmit = handleSubmit(async (v) => {
    const starts = new Date(`${v.date}T${v.time}`);
    if (isInPast(starts)) {
      setError("time", { message: "This time has already passed" });
      return;
    }
    const ok = await run(
      () =>
        book.mutateAsync({
          starts_at: starts.toISOString(),
          duration_minutes: Number(v.duration_minutes),
          mode: v.mode,
          reason: v.reason || null,
          location: v.location || null,
          follow_up_id: v.follow_up_id || null,
        }),
      "Appointment booked",
    );
    if (ok) close();
  });

  const e = formState.errors;
  return (
    <Dialog
      open={open}
      onClose={close}
      title="Book an appointment"
      description="Booked in your own schedule. Overlapping slots are refused."
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="appointment" loading={formState.isSubmitting}>
            Book
          </Button>
        </>
      }
    >
      <form id="appointment" onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
        {formError && (
          <div className="sm:col-span-2">
            <Alert tone="danger">
              {formError.includes("Conflict") ? "You already have an appointment at that time." : formError}
            </Alert>
          </div>
        )}
        <Field label="Date" error={e.date?.message}>
          {(p) => <Input {...p} type="date" min={todayIso()} {...register("date")} />}
        </Field>
        <Field label="Time" error={e.time?.message}>
          {(p) => <Input {...p} type="time" step={300} {...register("time")} />}
        </Field>
        <Field label="Length">
          {(p) => (
            <Select {...p} {...register("duration_minutes")}>
              {[10, 15, 20, 30, 45, 60].map((m) => (
                <option key={m} value={m}>
                  {m} minutes
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="Type">
          {(p) => (
            <Select {...p} {...register("mode")}>
              <option value="in_person">In person</option>
              <option value="teleconsult">Teleconsultation</option>
              <option value="home_visit">Home visit</option>
            </Select>
          )}
        </Field>
        {openFollowUps.length > 0 && (
          <Field label="For follow-up" className="sm:col-span-2">
            {(p) => (
              <Select {...p} {...register("follow_up_id")}>
                <option value="">Not linked</option>
                {openFollowUps.map((f) => (
                  <option key={f.id} value={f.id}>
                    Due {f.due_date}
                    {f.reason ? ` · ${f.reason}` : ""}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
        <Field label="Reason" className="sm:col-span-2">
          {(p) => <Input {...p} {...register("reason")} />}
        </Field>
        <Field label="Location" className="sm:col-span-2">
          {(p) => <Input {...p} {...register("location")} />}
        </Field>
      </form>
    </Dialog>
  );
}
