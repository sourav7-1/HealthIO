import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router";
import { z } from "zod";

import { Alert, Button, Checkbox, Dialog, Field, Input, Select } from "@/components/ui";
import type { Schemas } from "@/lib/api";
import { todayIso } from "@/lib/format";

import { useAddPatient } from "../api";
import { DATA_CATEGORIES, PRIVACY_NOTICE_VERSION } from "../consent";
import { useSubmit } from "../useSubmit";

const schema = z.object({
  given_name: z.string().trim().min(1, "Enter the patient's first name").max(100),
  family_name: z.string().trim().max(100).optional(),
  date_of_birth: z
    .string()
    .optional()
    .refine((v) => !v || v <= todayIso(), "Date of birth cannot be in the future"),
  sex_at_birth: z.enum(["female", "male", "intersex", "unknown"]),
  categories: z.array(z.string()).min(1, "Select at least one type of information"),
  confirmed: z.literal(true, { message: "Confirm the patient agreed before continuing" }),
});
type Values = z.infer<typeof schema>;

export function AddPatientDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const add = useAddPatient();
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      sex_at_birth: "unknown",
      categories: DATA_CATEGORIES.map((c) => c.value),
    },
  });
  const { run, formError, clearFormError } = useSubmit(setError);

  const close = () => {
    reset();
    clearFormError();
    onClose();
  };

  const onSubmit = handleSubmit(async (v) => {
    let createdId: string | null = null;
    const ok = await run(async () => {
      const created = await add.mutateAsync({
        given_name: v.given_name,
        family_name: v.family_name || null,
        date_of_birth: v.date_of_birth || null,
        sex_at_birth: v.sex_at_birth,
        consent: {
          confirmed: true,
          data_categories: v.categories as Schemas["DataCategory"][],
          notice_version: PRIVACY_NOTICE_VERSION,
        },
      });
      createdId = created.id;
    }, "Patient added");
    if (ok && createdId) {
      close();
      navigate(`/doctor/patients/${createdId}`);
    }
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Add a patient"
      description="For a patient who is with you now and does not use Health Io yet."
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="add-patient" loading={isSubmitting}>
            Add patient
          </Button>
        </>
      }
    >
      <form id="add-patient" onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        {formError && <Alert tone="danger">{formError}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="First name" required error={errors.given_name?.message}>
            {(p) => <Input {...p} autoComplete="off" {...register("given_name")} />}
          </Field>
          <Field label="Last name" error={errors.family_name?.message}>
            {(p) => <Input {...p} autoComplete="off" {...register("family_name")} />}
          </Field>
          <Field label="Date of birth" error={errors.date_of_birth?.message}>
            {(p) => <Input {...p} type="date" max={todayIso()} {...register("date_of_birth")} />}
          </Field>
          <Field label="Sex at birth" error={errors.sex_at_birth?.message}>
            {(p) => (
              <Select {...p} {...register("sex_at_birth")}>
                <option value="unknown">Not recorded</option>
                <option value="female">Female</option>
                <option value="male">Male</option>
                <option value="intersex">Intersex</option>
              </Select>
            )}
          </Field>
        </div>

        <fieldset className="rounded-xl border border-line p-4">
          <legend className="px-1 text-sm font-semibold">Consent to share with you</legend>
          <p className="mb-3 text-sm text-muted">
            Show the patient the privacy notice and ask what they agree to share with you. You will only see
            what is ticked. The patient can change this later.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {DATA_CATEGORIES.map((c) => (
              <Checkbox key={c.value} value={c.value} label={c.label} description={c.description} {...register("categories")} />
            ))}
          </div>
          {errors.categories && (
            <p className="mt-2 text-sm text-danger" role="alert">
              {errors.categories.message}
            </p>
          )}
          <div className="mt-4 border-t border-line pt-4">
            <Checkbox
              label="The patient (or their guardian) agreed in person to share the information ticked above"
              {...register("confirmed")}
            />
            {errors.confirmed && (
              <p className="mt-2 text-sm text-danger" role="alert">
                {errors.confirmed.message}
              </p>
            )}
          </div>
        </fieldset>
      </form>
    </Dialog>
  );
}
