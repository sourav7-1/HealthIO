import { zodResolver } from "@hookform/resolvers/zod";
import { Plus, Trash2 } from "lucide-react";
import { useFieldArray, useForm } from "react-hook-form";
import { z } from "zod";

import { Alert, Button, Checkbox, Dialog, Field, Input, Select, Textarea } from "@/components/ui";
import { todayIso } from "@/lib/format";

import { useCreatePrescription, useRecordMedication, useUpdatePrescription, type Prescription } from "../api";
import { useSubmit } from "../useSubmit";

const optionalInt = z
  .string()
  .trim()
  .optional()
  .refine((v) => !v || /^\d+$/.test(v), "Whole number");

const itemSchema = z
  .object({
    drug_name: z.string().trim().min(1, "Medicine name required").max(200),
    strength: z.string().trim().max(64).optional(),
    dosage_form: z.string().trim().max(64).optional(),
    route: z.string().trim().max(64).optional(),
    dose: z.string().trim().max(64).optional(),
    frequency_text: z.string().trim().max(64).optional(),
    meal_relation: z.enum(["", "before_food", "after_food", "with_food", "empty_stomach", "bedtime", "any"]),
    duration_days: optionalInt,
    is_prn: z.boolean(),
    prn_reason: z.string().trim().max(200).optional(),
    instructions: z.string().trim().max(1000).optional(),
  })
  .refine((i) => !i.is_prn || !!i.prn_reason, { message: "Say when to take it", path: ["prn_reason"] });

const schema = z.object({
  items: z.array(itemSchema).min(1, "Add at least one medicine").max(30),
  diagnosis_as_written: z.string().trim().max(1000).optional(),
  advice: z.string().trim().max(2000).optional(),
  valid_until: z.string().optional(),
});
type Values = z.infer<typeof schema>;

const emptyItem: Values["items"][number] = {
  drug_name: "",
  strength: "",
  dosage_form: "",
  route: "",
  dose: "",
  frequency_text: "",
  meal_relation: "",
  duration_days: "",
  is_prn: false,
  prn_reason: "",
  instructions: "",
};

function fromExisting(rx: Prescription): Values {
  return {
    items: rx.items.map((i) => ({
      drug_name: i.drug_name,
      strength: i.strength ?? "",
      dosage_form: i.dosage_form ?? "",
      route: i.route ?? "",
      dose: [i.dose_amount, i.dose_unit].filter(Boolean).join(" "),
      frequency_text: i.frequency_text ?? "",
      meal_relation: (i.meal_relation ?? "") as Values["items"][number]["meal_relation"],
      duration_days: i.duration_days ? String(i.duration_days) : "",
      is_prn: i.is_prn,
      prn_reason: i.prn_reason ?? "",
      instructions: i.instructions ?? "",
    })),
    diagnosis_as_written: rx.diagnosis_as_written ?? "",
    advice: rx.advice ?? "",
    valid_until: rx.valid_until ?? "",
  };
}

/** "1 tablet" → amount 1, unit "tablet". Anything else goes to instructions unchanged. */
function splitDose(dose: string | undefined): { dose_amount: string | null; dose_unit: string | null } {
  const m = dose?.match(/^\s*(\d+(?:\.\d+)?)\s*(.*)$/);
  return m ? { dose_amount: m[1] ?? null, dose_unit: m[2]?.trim() || null } : { dose_amount: null, dose_unit: null };
}

export function PrescriptionDialog({
  patientId,
  visitId,
  draft,
  open,
  onClose,
}: {
  patientId: string;
  visitId?: string;
  draft?: Prescription;
  open: boolean;
  onClose: () => void;
}) {
  const create = useCreatePrescription(patientId);
  const update = useUpdatePrescription(patientId);
  const { register, control, handleSubmit, reset, setError, watch, formState } = useForm<Values>({
    resolver: zodResolver(schema),
    values: draft ? fromExisting(draft) : undefined,
    defaultValues: { items: [emptyItem] },
  });
  const items = useFieldArray({ control, name: "items" });
  const { run, formError, clearFormError } = useSubmit(setError);

  const close = () => {
    reset({ items: [emptyItem] });
    clearFormError();
    onClose();
  };

  const onSubmit = handleSubmit(async (v) => {
    const payloadItems = v.items.map((i) => {
      const { dose_amount, dose_unit } = splitDose(i.dose);
      const unparsedDose = i.dose && !dose_amount ? `Dose: ${i.dose}` : null;
      return {
        drug_name: i.drug_name,
        strength: i.strength || null,
        dosage_form: i.dosage_form || null,
        route: i.route || null,
        dose_amount,
        dose_unit,
        frequency_text: i.frequency_text || null,
        meal_relation: i.meal_relation || null,
        duration_days: i.duration_days ? Number(i.duration_days) : null,
        is_prn: i.is_prn,
        prn_reason: i.is_prn ? i.prn_reason || null : null,
        instructions: [unparsedDose, i.instructions].filter(Boolean).join(". ") || null,
      };
    });
    const common = {
      items: payloadItems,
      diagnosis_as_written: v.diagnosis_as_written || null,
      advice: v.advice || null,
      valid_until: v.valid_until || null,
    };
    const ok = await run(
      () =>
        draft
          ? update.mutateAsync({ prescription_id: draft.id, ...common })
          : create.mutateAsync({ ...common, visit_id: visitId ?? null }),
      draft ? "Draft updated" : "Prescription saved as draft",
    );
    if (ok) close();
  });

  const e = formState.errors;
  return (
    <Dialog
      open={open}
      onClose={close}
      size="lg"
      title={draft ? "Edit draft prescription" : "New prescription"}
      description="Saved as a draft only you can see. Review it, then issue it from the Prescriptions tab."
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="prescription" loading={formState.isSubmitting}>
            Save draft
          </Button>
        </>
      }
    >
      <form id="prescription" onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        {formError && <Alert tone="danger">{formError}</Alert>}
        {items.fields.map((field, i) => {
          const ie = e.items?.[i];
          const prn = watch(`items.${i}.is_prn`);
          return (
            <fieldset key={field.id} className="rounded-xl border border-line p-4">
              <legend className="px-1 text-sm font-semibold">Medicine {i + 1}</legend>
              <div className="grid gap-3 sm:grid-cols-6">
                <Field label="Medicine" required error={ie?.drug_name?.message} className="sm:col-span-3">
                  {(p) => <Input {...p} placeholder="Name as prescribed" {...register(`items.${i}.drug_name`)} />}
                </Field>
                <Field label="Strength" className="sm:col-span-1">
                  {(p) => <Input {...p} placeholder="e.g. 500 mg" {...register(`items.${i}.strength`)} />}
                </Field>
                <Field label="Form" className="sm:col-span-1">
                  {(p) => <Input {...p} placeholder="Tablet" {...register(`items.${i}.dosage_form`)} />}
                </Field>
                <Field label="Route" className="sm:col-span-1">
                  {(p) => <Input {...p} placeholder="Oral" {...register(`items.${i}.route`)} />}
                </Field>
                <Field label="Dose" className="sm:col-span-2">
                  {(p) => <Input {...p} placeholder="e.g. 1 tablet" {...register(`items.${i}.dose`)} />}
                </Field>
                <Field label="Frequency" className="sm:col-span-2" hint="As you write it, e.g. 1-0-1">
                  {(p) => <Input {...p} {...register(`items.${i}.frequency_text`)} />}
                </Field>
                <Field label="Days" error={ie?.duration_days?.message} className="sm:col-span-1">
                  {(p) => <Input {...p} inputMode="numeric" {...register(`items.${i}.duration_days`)} />}
                </Field>
                <Field label="With food" className="sm:col-span-1">
                  {(p) => (
                    <Select {...p} {...register(`items.${i}.meal_relation`)}>
                      <option value="">—</option>
                      <option value="before_food">Before food</option>
                      <option value="after_food">After food</option>
                      <option value="with_food">With food</option>
                      <option value="empty_stomach">Empty stomach</option>
                      <option value="bedtime">At bedtime</option>
                      <option value="any">Any time</option>
                    </Select>
                  )}
                </Field>
                <div className="sm:col-span-6">
                  <Checkbox label="Take only when needed (SOS / PRN)" {...register(`items.${i}.is_prn`)} />
                </div>
                {prn && (
                  <Field label="When needed" required error={ie?.prn_reason?.message} className="sm:col-span-6">
                    {(p) => <Input {...p} placeholder="e.g. for fever above 100°F" {...register(`items.${i}.prn_reason`)} />}
                  </Field>
                )}
                <Field label="Instructions" className="sm:col-span-6">
                  {(p) => <Input {...p} {...register(`items.${i}.instructions`)} />}
                </Field>
              </div>
              {items.fields.length > 1 && (
                <div className="mt-3 flex justify-end">
                  <Button variant="ghost" size="sm" icon={<Trash2 className="size-4" />} onClick={() => items.remove(i)}>
                    Remove
                  </Button>
                </div>
              )}
            </fieldset>
          );
        })}
        <div>
          <Button variant="secondary" size="sm" icon={<Plus className="size-4" />} onClick={() => items.append(emptyItem)}>
            Add another medicine
          </Button>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Diagnosis (as you write it on the prescription)" className="sm:col-span-2">
            {(p) => <Input {...p} {...register("diagnosis_as_written")} />}
          </Field>
          <Field label="Advice" className="sm:col-span-2">
            {(p) => <Textarea {...p} rows={2} {...register("advice")} />}
          </Field>
          <Field label="Valid until">
            {(p) => <Input {...p} type="date" min={todayIso()} {...register("valid_until")} />}
          </Field>
        </div>
      </form>
    </Dialog>
  );
}

// --- Record a medicine the patient already takes ------------------------------------------

const medSchema = z.object({
  name: z.string().trim().min(1, "Medicine name required").max(200),
  strength: z.string().trim().max(64).optional(),
  dosage_form: z.string().trim().max(64).optional(),
  instructions: z.string().trim().max(1000).optional(),
  start_date: z.string().optional().refine((v) => !v || v <= todayIso(), "Cannot be in the future"),
  is_prn: z.boolean(),
});
type MedValues = z.infer<typeof medSchema>;

export function RecordMedicationDialog({
  patientId,
  open,
  onClose,
}: {
  patientId: string;
  open: boolean;
  onClose: () => void;
}) {
  const record = useRecordMedication(patientId);
  const { register, handleSubmit, reset, setError, formState } = useForm<MedValues>({
    resolver: zodResolver(medSchema),
    defaultValues: { is_prn: false },
  });
  const { run, formError, clearFormError } = useSubmit(setError);
  const close = () => {
    reset();
    clearFormError();
    onClose();
  };
  const onSubmit = handleSubmit(async (v) => {
    const ok = await run(
      () =>
        record.mutateAsync({
          name: v.name,
          strength: v.strength || null,
          dosage_form: v.dosage_form || null,
          instructions: v.instructions || null,
          start_date: v.start_date || null,
          is_prn: v.is_prn,
        }),
      "Medicine recorded",
    );
    if (ok) close();
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Record a current medicine"
      description="A medicine the patient already takes (for example, started elsewhere). To start new treatment, write a prescription."
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="record-medication" loading={formState.isSubmitting}>
            Record
          </Button>
        </>
      }
    >
      <form id="record-medication" onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
        {formError && (
          <div className="sm:col-span-2">
            <Alert tone="danger">{formError}</Alert>
          </div>
        )}
        <Field label="Medicine" required error={formState.errors.name?.message} className="sm:col-span-2">
          {(p) => <Input {...p} {...register("name")} />}
        </Field>
        <Field label="Strength">{(p) => <Input {...p} {...register("strength")} />}</Field>
        <Field label="Form">{(p) => <Input {...p} {...register("dosage_form")} />}</Field>
        <Field label="Taking since" error={formState.errors.start_date?.message}>
          {(p) => <Input {...p} type="date" max={todayIso()} {...register("start_date")} />}
        </Field>
        <div className="flex items-end pb-2">
          <Checkbox label="Only when needed" {...register("is_prn")} />
        </div>
        <Field label="How the patient takes it" className="sm:col-span-2">
          {(p) => <Textarea {...p} rows={2} {...register("instructions")} />}
        </Field>
      </form>
    </Dialog>
  );
}
