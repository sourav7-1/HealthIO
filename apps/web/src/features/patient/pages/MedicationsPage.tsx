import { Plus, Pill, Stethoscope, UserRound } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Button, Card, Checkbox, Dialog, EmptyState, Field, Input, Select, Textarea, useToast } from "@/components/ui";
import { useMedications } from "@/features/chart/api";
import { QueryState, StatusBadge } from "@/features/chart/shared";
import { errorMessage, type Schemas } from "@/lib/api";
import { formatDate, formatTime, humanize, todayIso } from "@/lib/format";

import {
  useAddSelfReported,
  useChangeReminderTimes,
  useConfirmMedication,
  useLogAsNeeded,
  useStopMedication,
  type MedicationOut,
} from "../api";
import { SafetyNote, SourceBadge, TimesPicker } from "../components";
import { useActivePatient } from "../context";

type Meal = Schemas["MealRelation"];

function timesOf(med: MedicationOut): string[] {
  return (med.schedule?.times_of_day ?? []).map((t) => t.slice(0, 5));
}

function readableTimes(med: MedicationOut): string {
  const times = timesOf(med);
  if (med.is_prn) return "Only when needed";
  if (times.length === 0) return "No reminders set";
  return times.map((t) => formatTime(`2000-01-01T${t}:00`)).join(", ");
}

export function MedicationsPage() {
  const { patientId: pid, mode, can } = useActivePatient();
  const meds = useMedications(pid);
  const [adding, setAdding] = useState(false);
  const self = mode === "self";

  return (
    <>
      <PageHeader
        title={self ? "My medicines" : "Medicines"}
        description={
          self
            ? "Medicines your doctor prescribed, and medicines you added yourself."
            : "Medicines a doctor prescribed, and medicines added by the patient or family. Prescribed medicines can only be changed by the doctor."
        }
        actions={
          can("report_health_info") && (
            <Button icon={<Plus className="size-4" />} onClick={() => setAdding(true)}>
              {self ? "Add a medicine I take" : "Add a medicine they take"}
            </Button>
          )
        }
      />
      <QueryState
        query={meds}
        what="Medicines"
        isEmpty={(m) => m.length === 0}
        empty={
          <Card>
            <EmptyState
              icon={<Pill className="size-5" />}
              title="No medicines yet"
              description="Medicines your doctor prescribes appear here. You can also add medicines you already take."
              action={
                can("report_health_info") ? (
                  <Button variant="secondary" onClick={() => setAdding(true)}>Add a medicine</Button>
                ) : undefined
              }
            />
          </Card>
        }
      >
        {(list) => {
          const pending = list.filter((m) => m.status === "pending_confirmation");
          const current = list.filter((m) => m.status === "active" || m.status === "paused");
          const prescribed = current.filter((m) => m.source !== "self_reported");
          const mine = current.filter((m) => m.source === "self_reported");
          const past = list.filter((m) => ["completed", "stopped"].includes(m.status));
          return (
            <div className="flex flex-col gap-6">
              {pending.length > 0 && (
                <Card title="New from your doctor: set up reminders">
                  <ul className="flex flex-col gap-4">
                    {pending.map((m) => (
                      <PendingMedicine key={m.id} med={m} patientId={pid} />
                    ))}
                  </ul>
                </Card>
              )}

              <Card title={<span className="flex items-center gap-2"><Stethoscope className="size-4" aria-hidden /> Prescribed by your doctor</span>}>
                {prescribed.length === 0 ? (
                  <p className="text-sm text-muted">No active prescribed medicines.</p>
                ) : (
                  <ul className="flex flex-col gap-4">
                    {prescribed.map((m) => (
                      <MedicineRow key={m.id} med={m} patientId={pid} />
                    ))}
                  </ul>
                )}
                <div className="mt-4">
                  <SafetyNote />
                </div>
              </Card>

              <Card title={<span className="flex items-center gap-2"><UserRound className="size-4" aria-hidden /> Added by you</span>}>
                {mine.length === 0 ? (
                  <p className="text-sm text-muted">
                    Add medicines you take that were not prescribed here, such as vitamins or medicines from another doctor.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-4">
                    {mine.map((m) => (
                      <MedicineRow key={m.id} med={m} patientId={pid} />
                    ))}
                  </ul>
                )}
              </Card>

              {past.length > 0 && (
                <Card title="Past medicines">
                  <ul className="divide-y divide-line">
                    {past.map((m) => (
                      <li key={m.id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm first:pt-0 last:pb-0">
                        <span className="font-medium">{m.name}</span>
                        <span className="flex items-center gap-2 text-muted">
                          <SourceBadge source={m.source} /> {humanize(m.status)} {formatDate(m.end_date)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </div>
          );
        }}
      </QueryState>
      <AddMedicineDialog patientId={pid} open={adding} onClose={() => setAdding(false)} />
    </>
  );
}

function Instructions({ med }: { med: MedicationOut }) {
  return (
    <div className="mt-2 space-y-1 text-sm">
      {med.prescribed_directions && (
        <p>
          <span className="text-muted">Doctor&apos;s instructions: </span>
          {med.prescribed_directions}
        </p>
      )}
      {med.instructions && !med.prescribed_directions && (
        <p>
          <span className="text-muted">Instructions: </span>
          {med.instructions}
        </p>
      )}
      {(med.start_date || med.end_date) && (
        <p className="text-muted">
          {med.start_date ? `From ${formatDate(med.start_date)}` : ""}
          {med.end_date ? ` until ${formatDate(med.end_date)}` : ""}
        </p>
      )}
    </div>
  );
}

function PendingMedicine({ med, patientId }: { med: MedicationOut; patientId: string }) {
  const { can } = useActivePatient();
  const confirm = useConfirmMedication(patientId);
  const toast = useToast();
  const [times, setTimes] = useState<string[]>(med.is_prn ? [] : ["08:00", "20:00"]);
  const [error, setError] = useState<string | undefined>();
  const save = async () => {
    if (!med.is_prn && times.length === 0) return setError("Choose at least one time.");
    try {
      await confirm.mutateAsync({ medication_id: med.id, times_of_day: times, meal_relation: null, timezone: null });
      toast.success(`Reminders set for ${med.name}`);
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <li className="rounded-xl border border-warning/50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-base font-semibold">
          {med.name} {med.strength && <span className="font-normal text-muted">{med.strength}</span>}
        </p>
        <SourceBadge source={med.source} />
      </div>
      <Instructions med={med} />
      {can("manage_reminders") ? (
        <>
          <div className="mt-4">
            {med.is_prn ? (
              <p className="text-sm">This medicine is taken only when needed, so there are no scheduled reminders.</p>
            ) : (
              <TimesPicker value={times} onChange={setTimes} error={error} />
            )}
          </div>
          <Button className="mt-4" onClick={() => void save()} loading={confirm.isPending}>
            {med.is_prn ? "Add to medicines" : "Start reminders"}
          </Button>
        </>
      ) : (
        <p className="mt-3 text-sm text-muted">Waiting for reminder times to be set up.</p>
      )}
    </li>
  );
}

function MedicineRow({ med, patientId }: { med: MedicationOut; patientId: string }) {
  const { can } = useActivePatient();
  const [dialog, setDialog] = useState<"times" | "stop" | null>(null);
  const logPrn = useLogAsNeeded(patientId);
  const toast = useToast();
  return (
    <li className="rounded-xl border border-line p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-base font-semibold">
            {med.name} {med.strength && <span className="font-normal text-muted">{med.strength}</span>}
          </p>
          <p className="text-sm">{readableTimes(med)}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <SourceBadge source={med.source} />
          {med.status !== "active" && <StatusBadge status={med.status} />}
        </div>
      </div>
      <Instructions med={med} />
      <div className="mt-3 flex flex-wrap gap-2">
        {med.is_prn ? (
          can("log_doses") && (
          <Button
            size="sm"
            onClick={() =>
              void logPrn.mutateAsync(med.id).then(
                () => toast.success(`Recorded: you took ${med.name}`),
                (err) => toast.error(errorMessage(err)),
              )
            }
            loading={logPrn.isPending}
          >
            I took a dose now
          </Button>
          )
        ) : (
          can("manage_reminders") && (
            <Button size="sm" variant="secondary" onClick={() => setDialog("times")}>
              Change reminder times
            </Button>
          )
        )}
        {med.source === "self_reported" && can("report_health_info") && (
          <Button size="sm" variant="ghost" onClick={() => setDialog("stop")}>
            I stopped taking this
          </Button>
        )}
      </div>
      {dialog === "times" && <ChangeTimesDialog med={med} patientId={patientId} onClose={() => setDialog(null)} />}
      {dialog === "stop" && <StopDialog med={med} patientId={patientId} onClose={() => setDialog(null)} />}
    </li>
  );
}

function ChangeTimesDialog({ med, patientId, onClose }: { med: MedicationOut; patientId: string; onClose: () => void }) {
  const change = useChangeReminderTimes(patientId);
  const toast = useToast();
  const [times, setTimes] = useState<string[]>(timesOf(med));
  const [error, setError] = useState<string | undefined>();
  const save = async () => {
    if (times.length === 0) return setError("Choose at least one time.");
    try {
      await change.mutateAsync({ medication_id: med.id, times_of_day: times, meal_relation: null, timezone: null });
      toast.success("Reminder times updated");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Reminder times for ${med.name}`}
      description={
        med.source !== "self_reported"
          ? "This changes only when you are reminded. Your doctor's instructions stay the same."
          : undefined
      }
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} loading={change.isPending}>Save</Button>
        </>
      }
    >
      <TimesPicker value={times} onChange={setTimes} error={error} />
    </Dialog>
  );
}

function StopDialog({ med, patientId, onClose }: { med: MedicationOut; patientId: string; onClose: () => void }) {
  const stop = useStopMedication(patientId);
  const toast = useToast();
  const [reason, setReason] = useState("");
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Stop ${med.name}?`}
      description="Reminders for this medicine will stop. It stays in your history."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Back</Button>
          <Button
            variant="danger"
            loading={stop.isPending}
            onClick={() =>
              void stop.mutateAsync({ medication_id: med.id, reason: reason.trim() || null }).then(
                () => {
                  toast.success(`${med.name} stopped`);
                  onClose();
                },
                (err) => toast.error(errorMessage(err)),
              )
            }
          >
            Stop medicine
          </Button>
        </>
      }
    >
      <Field label="Reason (optional)">
        {(p) => <Textarea {...p} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
      </Field>
    </Dialog>
  );
}

function AddMedicineDialog({ patientId, open, onClose }: { patientId: string; open: boolean; onClose: () => void }) {
  const add = useAddSelfReported(patientId);
  const toast = useToast();
  const [name, setName] = useState("");
  const [strength, setStrength] = useState("");
  const [instructions, setInstructions] = useState("");
  const [meal, setMeal] = useState<string>("");
  const [since, setSince] = useState("");
  const [prn, setPrn] = useState(false);
  const [times, setTimes] = useState<string[]>(["08:00"]);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    setName("");
    setStrength("");
    setInstructions("");
    setMeal("");
    setSince("");
    setPrn(false);
    setTimes(["08:00"]);
    setError(null);
    onClose();
  };

  const save = async () => {
    if (!name.trim()) return setError("Enter the medicine's name.");
    if (!prn && times.length === 0) return setError("Choose at least one reminder time, or tick 'Only when needed'.");
    try {
      await add.mutateAsync({
        name: name.trim(),
        strength: strength.trim() || null,
        dosage_form: null,
        instructions: instructions.trim() || null,
        start_date: since || null,
        is_prn: prn,
        times_of_day: prn ? [] : times,
        meal_relation: (meal || null) as Meal | null,
        timezone: null,
      });
      toast.success(`${name.trim()} added`);
      close();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Add a medicine you take"
      description="It will be marked 'Added by you' so your doctor knows it did not come from a prescription here."
      footer={
        <>
          <Button variant="secondary" onClick={close}>Cancel</Button>
          <Button onClick={() => void save()} loading={add.isPending}>Add medicine</Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <Field label="Medicine name" required>
          {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Strength">
            {(p) => <Input {...p} placeholder="e.g. 500 mg" value={strength} onChange={(e) => setStrength(e.target.value)} />}
          </Field>
          <Field label="Taking since">
            {(p) => <Input {...p} type="date" max={todayIso()} value={since} onChange={(e) => setSince(e.target.value)} />}
          </Field>
        </div>
        <Field label="With food">
          {(p) => (
            <Select {...p} value={meal} onChange={(e) => setMeal(e.target.value)}>
              <option value="">No preference</option>
              <option value="before_food">Before food</option>
              <option value="after_food">After food</option>
              <option value="with_food">With food</option>
              <option value="empty_stomach">Empty stomach</option>
              <option value="bedtime">At bedtime</option>
            </Select>
          )}
        </Field>
        <Field label="How you take it">
          {(p) => <Textarea {...p} rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} />}
        </Field>
        <Checkbox label="Only when needed (no reminders)" checked={prn} onChange={(e) => setPrn(e.target.checked)} />
        {!prn && <TimesPicker value={times} onChange={setTimes} />}
      </div>
    </Dialog>
  );
}
