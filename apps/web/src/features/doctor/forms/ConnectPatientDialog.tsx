import { zodResolver } from "@hookform/resolvers/zod";
import { MailCheck } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Alert, Button, Dialog, EmptyState, Field, Input } from "@/components/ui";

import { useConnectPatient } from "@/features/chart/api";
import { useSubmit } from "@/features/chart/useSubmit";

const schema = z.object({ patient_email: z.email("Enter the email the patient uses for Health Io") });
type Values = z.infer<typeof schema>;

export function ConnectPatientDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const connect = useConnectPatient();
  const [sent, setSent] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema) });
  const { run, formError, clearFormError } = useSubmit(setError);

  const close = () => {
    reset();
    setSent(null);
    clearFormError();
    onClose();
  };

  const onSubmit = handleSubmit(async (v) => {
    const ok = await run(() => connect.mutateAsync(v), "Request sent");
    if (ok) setSent(v.patient_email);
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Connect an existing patient"
      description="For a patient who already has a Health Io account."
      footer={
        sent ? (
          <Button onClick={close}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" form="connect-patient" loading={isSubmitting}>
              Send request
            </Button>
          </>
        )
      }
    >
      {sent ? (
        <EmptyState
          icon={<MailCheck className="size-5" />}
          title="Request sent"
          description={`If ${sent} belongs to a patient, they will be asked to connect with you and to choose what to share. They will appear in your patient list once they accept.`}
        />
      ) : (
        <form id="connect-patient" onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
          {formError && <Alert tone="danger">{formError}</Alert>}
          <Field
            label="Patient's email"
            error={errors.patient_email?.message}
            hint="Nothing is shared with you until the patient accepts."
          >
            {(p) => <Input {...p} type="email" autoComplete="off" {...register("patient_email")} />}
          </Field>
        </form>
      )}
    </Dialog>
  );
}
