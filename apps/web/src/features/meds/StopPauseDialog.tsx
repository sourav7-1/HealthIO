/**
 * Pause or discontinue a medicine. For a prescribed medicine this shows a warning and asks
 * who decided: a doctor or pharmacist (named), the doctor in the app (a request), or the
 * patient's own decision, which is recorded as such. The app never advises stopping.
 */
import { useState } from "react";

import { Alert, Button, Checkbox, Dialog, Field, Input, Textarea, useToast } from "@/components/ui";
import { useActivePatient } from "@/features/patient/context";
import { errorMessage } from "@/lib/api";
import { todayIso } from "@/lib/format";

import { usePause, useRequestChange, useStop, type Med } from "./api";
import { isPrescribed } from "./labels";

type Who = "doctor" | "pharmacist" | "own" | "ask";

export function StopPauseDialog({ med, action, onClose }: { med: Med; action: "stop" | "pause"; onClose: () => void }) {
  const { patientId, mode } = useActivePatient();
  const stop = useStop(patientId, med.id);
  const pause = usePause(patientId, med.id);
  const ask = useRequestChange(patientId, med.id);
  const toast = useToast();
  const prescribed = isPrescribed(med.origin);
  const self = mode === "self";
  const [who, setWho] = useState<Who>(prescribed ? "ask" : "own");
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [resumeOn, setResumeOn] = useState("");
  const [ack, setAck] = useState(!prescribed);
  const [error, setError] = useState<string | null>(null);
  const verb = action === "stop" ? "Stop" : "Pause";

  const submit = async () => {
    setError(null);
    try {
      if (who === "ask") {
        await ask.mutateAsync({ kind: action, message: reason.trim() || null, schedule: null });
        toast.success("Sent to the doctor. Nothing changes until they confirm.");
      } else {
        const confirmation = {
          acknowledged: ack,
          advice: who === "doctor" || who === "pharmacist" ? { role: who, name: name.trim() } : null,
          own_decision: who === "own",
          reason: reason.trim() || null,
        };
        if (action === "stop") await stop.mutateAsync(confirmation);
        else await pause.mutateAsync({ ...confirmation, resume_on: resumeOn || null });
        toast.success(action === "stop" ? "Medicine discontinued" : "Medicine paused");
      }
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const ready = who === "ask" || (ack && (who === "own" || name.trim().length >= 2));
  return (
    <Dialog
      open
      onClose={onClose}
      title={`${verb} ${med.name}?`}
      description={action === "stop" ? "Reminders stop and it moves to past medicines." : "Reminders stop until you restart it."}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Back</Button>
          <Button variant={who === "ask" ? "primary" : "danger"} disabled={!ready} loading={stop.isPending || pause.isPending || ask.isPending} onClick={() => void submit()}>
            {who === "ask" ? "Ask the doctor" : verb}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        {prescribed && (
          <Alert tone="warning" title="This medicine was prescribed">
            {action === "stop" ? "Stopping" : "Pausing"} a prescribed medicine without talking to the doctor can be harmful. If you are unsure, ask the
            doctor or a pharmacist first.
          </Alert>
        )}
        {prescribed && (
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Who decided?</legend>
            {(
              [
                ["ask", `Ask ${self ? "my" : "their"} doctor in the app first`],
                ["doctor", "A doctor told me to"],
                ["pharmacist", "A pharmacist told me to"],
                ["own", self ? "It is my own decision" : "It is the patient's own decision"],
              ] as [Who, string][]
            ).map(([value, label]) => (
              <label key={value} className="flex min-h-11 items-center gap-3 rounded-lg border border-line px-3 text-sm">
                <input type="radio" name="who" className="size-4" checked={who === value} onChange={() => setWho(value)} />
                {label}
              </label>
            ))}
          </fieldset>
        )}
        {(who === "doctor" || who === "pharmacist") && (
          <Field label={`The ${who}'s name`} required>
            {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}
          </Field>
        )}
        {action === "pause" && who !== "ask" && (
          <Field label="Restart on (optional)">
            {(p) => <Input {...p} type="date" min={todayIso(1)} value={resumeOn} onChange={(e) => setResumeOn(e.target.value)} />}
          </Field>
        )}
        <Field label="Reason (optional)">
          {(p) => <Textarea {...p} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}
        </Field>
        {prescribed && who !== "ask" && (
          <Checkbox label="I understand the warning above" checked={ack} onChange={(e) => setAck(e.target.checked)} />
        )}
        {who === "own" && prescribed && (
          <p className="text-xs text-muted">This is recorded as {self ? "your" : "the patient's"} own decision and the doctor can see it in the medicine&apos;s history.</p>
        )}
      </div>
    </Dialog>
  );
}
