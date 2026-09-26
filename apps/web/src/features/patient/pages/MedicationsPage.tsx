import { Pill, Plus, TriangleAlert } from "lucide-react";
import { Link } from "react-router";
import { useState } from "react";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Button, Card, Checkbox, Dialog, EmptyState, Field, Input, Select, Textarea, useToast } from "@/components/ui";
import { useMedications } from "@/features/chart/api";
import { QueryState } from "@/features/chart/shared";
import { errorMessage, type Schemas } from "@/lib/api";
import { formatDate, todayIso } from "@/lib/format";

import {
  useAddSelfReported,
  useConfirmMedication,
  useLogAsNeeded,
  type MedicationOut,
} from "../api";
import { OriginBadge, StatusPill, courseText, scheduleText } from "@/features/meds/labels";

import { SafetyNote, SourceBadge, TimesPicker } from "../components";
import { useActivePatient } from "../context";
import { SafetyWarningsCard } from "@/features/safety/SafetyWarnings";

type Meal = Schemas["MealRelation"];

export function MedicationsPage() {
  const { patientId: pid, mode, can } = useActivePatient();
  const meds = useMedications(pid);
  const [adding, setAdding] = useState(false);
  const [showPast, setShowPast] = useState(false);
  const self = mode === "self";

  return (
    <>
      <PageHeader
        title={self ? "My medicines" : "Medicines"}
        description="Every medicine shows where it came from. Prescribed medicines stay as the doctor wrote them; changes to them need the doctor or a pharmacist."
        actions={
          can("report_health_info") && (
            <Button icon={<Plus className="size-4" />} onClick={() => setAdding(true)}>
              {self ? "Add a medicine I take" : "Add a medicine they take"}
            </Button>
          )
        }
      />
      <div className="mb-6">
        <SafetyWarningsCard patientId={pid} viewer="patient" />
      </div>
      <QueryState
        query={meds}
        what="Medicines"
        isEmpty={(m) => m.length === 0}
        empty={
          <Card>
            <EmptyState
              icon={<Pill className="size-5" />}
              title="No medicines yet"
              description="Medicines from prescriptions appear here. You can also add medicines you already take."
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
          const active = list.filter((m) => m.status === "active");
          const paused = list.filter((m) => m.status === "paused");
          const past = list.filter((m) => ["completed", "stopped"].includes(m.status));
          return (
            <div className="flex flex-col gap-6">
              {pending.length > 0 && (
                <Card title="New from a prescription: set up reminders">
                  <ul className="flex flex-col gap-4">
                    {pending.map((m) => (
                      <PendingMedicine key={m.id} med={m} patientId={pid} />
                    ))}
                  </ul>
                </Card>
              )}
              <Card title={`In use (${active.length})`}>
                {active.length === 0 ? (
                  <p className="text-sm text-muted">No medicines in use.</p>
                ) : (
                  <ul className="flex flex-col gap-4">
                    {active.map((m) => (
                      <MedicineCard key={m.id} med={m} />
                    ))}
                  </ul>
                )}
                <div className="mt-4">
                  <SafetyNote />
                </div>
              </Card>
              {paused.length > 0 && (
                <Card title={`Paused (${paused.length})`}>
                  <ul className="flex flex-col gap-4">
                    {paused.map((m) => (
                      <MedicineCard key={m.id} med={m} />
                    ))}
                  </ul>
                </Card>
              )}
              {past.length > 0 && (
                <Card
                  title={`Past medicines (${past.length})`}
                  action={
                    <Button size="sm" variant="ghost" onClick={() => setShowPast((v) => !v)}>
                      {showPast ? "Hide" : "Show"}
                    </Button>
                  }
                >
                  {showPast ? (
                    <ul className="flex flex-col gap-4">
                      {past.map((m) => (
                        <MedicineCard key={m.id} med={m} />
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted">Completed courses and discontinued medicines, with their history.</p>
                  )}
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

function MedicineCard({ med }: { med: MedicationOut }) {
  const { patientId, base, can } = useActivePatient();
  const logPrn = useLogAsNeeded(patientId);
  const toast = useToast();
  return (
    <li className="rounded-xl border border-line p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <Link to={`${base}/medications/${med.id}`} className="text-base font-semibold hover:underline">
            {med.name} {med.strength && <span className="font-normal text-muted">{med.strength}</span>}
          </Link>
          <p className="text-sm">{scheduleText(med)}</p>
          {courseText(med) && <p className="text-sm text-muted">{courseText(med)}</p>}
        </div>
        <div className="flex flex-col items-end gap-1">
          <OriginBadge origin={med.origin} />
          {med.status !== "active" && <StatusPill status={med.status} />}
        </div>
      </div>
      <Instructions med={med} />
      {(med.duplicates ?? []).length > 0 && (
        <p className="mt-2 flex items-center gap-1 text-sm text-warning">
          <TriangleAlert className="size-4" aria-hidden /> May be on the list twice ({(med.duplicates ?? []).map((d) => d.name).join(", ")})
        </p>
      )}
      {med.pending_request && <p className="mt-2 text-sm text-info">A change is waiting for the doctor&apos;s confirmation.</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        {med.is_prn && med.status === "active" && can("log_doses") && (
          <Button
            size="sm"
            onClick={() =>
              void logPrn.mutateAsync(med.id).then(
                () => toast.success(`Recorded: ${med.name} taken`),
                (err) => toast.error(errorMessage(err)),
              )
            }
            loading={logPrn.isPending}
          >
            I took a dose now
          </Button>
        )}
        <Link to={`${base}/medications/${med.id}`} className="inline-flex min-h-9 items-center rounded-lg border border-line px-3 text-sm font-medium hover:bg-surface-2">
          Details and changes
        </Link>
      </div>
    </li>
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
      await confirm.mutateAsync({ medication_id: med.id, times_of_day: times, meal_relation: null, timezone: null, acknowledged: false });
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
        schedule_type: prn ? "as_needed" : "fixed_times",
        clear_end_date: false,
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
