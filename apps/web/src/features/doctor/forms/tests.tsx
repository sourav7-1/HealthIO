import { zodResolver } from "@hookform/resolvers/zod";
import { Plus, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { z } from "zod";

import { Alert, Button, Dialog, Field, Input, Select, Textarea } from "@/components/ui";
import { bytes, todayIso } from "@/lib/format";

import { uploadDocument, useOrderTests, useRecordReport, type TestOrder } from "@/features/chart/api";
import { useSubmit } from "@/features/chart/useSubmit";

// --- Order tests -----------------------------------------------------------------------------

const orderSchema = z.object({
  tests: z.string().trim().min(1, "List at least one test"),
  priority: z.enum(["routine", "urgent", "stat"]),
  clinical_indication: z.string().trim().max(1000).optional(),
  due_by: z.string().optional(),
});
type OrderValues = z.infer<typeof orderSchema>;

export function OrderTestsDialog({
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
  const order = useOrderTests(patientId);
  const { register, handleSubmit, reset, setError, formState } = useForm<OrderValues>({
    resolver: zodResolver(orderSchema),
    defaultValues: { priority: "routine" },
  });
  const { run, formError, clearFormError } = useSubmit(setError);
  const close = () => {
    reset();
    clearFormError();
    onClose();
  };
  const onSubmit = handleSubmit(async (v) => {
    const tests = v.tests
      .split(/\n|,/)
      .map((t) => t.trim())
      .filter(Boolean);
    const ok = await run(
      () =>
        order.mutateAsync({
          tests,
          priority: v.priority,
          clinical_indication: v.clinical_indication || null,
          due_by: v.due_by || null,
          visit_id: visitId ?? null,
        }),
      `${tests.length} test${tests.length === 1 ? "" : "s"} ordered`,
    );
    if (ok) close();
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Order tests"
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="order-tests" loading={formState.isSubmitting}>
            Order
          </Button>
        </>
      }
    >
      <form id="order-tests" onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2" noValidate>
        {formError && (
          <div className="sm:col-span-2">
            <Alert tone="danger">{formError}</Alert>
          </div>
        )}
        <Field
          label="Tests"
          required
          hint="One per line, or separated by commas."
          error={formState.errors.tests?.message}
          className="sm:col-span-2"
        >
          {(p) => <Textarea {...p} rows={4} {...register("tests")} />}
        </Field>
        <Field label="Priority">
          {(p) => (
            <Select {...p} {...register("priority")}>
              <option value="routine">Routine</option>
              <option value="urgent">Urgent</option>
              <option value="stat">STAT</option>
            </Select>
          )}
        </Field>
        <Field label="Needed by">
          {(p) => <Input {...p} type="date" min={todayIso()} {...register("due_by")} />}
        </Field>
        <Field label="Clinical indication" className="sm:col-span-2" error={formState.errors.clinical_indication?.message}>
          {(p) => <Textarea {...p} rows={2} {...register("clinical_indication")} />}
        </Field>
      </form>
    </Dialog>
  );
}

// --- Upload / record report --------------------------------------------------------------------

const ACCEPT = "application/pdf,image/jpeg,image/png,image/webp,image/heic";
const MAX_BYTES = 15 * 1024 * 1024;

const resultSchema = z.object({
  analyte_name: z.string().trim().min(1, "Name required"),
  value: z.string().trim().min(1, "Value required"),
  unit: z.string().trim().optional(),
  reference_text: z.string().trim().optional(),
  flag: z.enum(["unknown", "normal", "low", "high", "critical_low", "critical_high", "abnormal"]),
});
const reportSchema = z.object({
  lab_name: z.string().trim().max(200).optional(),
  collected_on: z.string().optional().refine((v) => !v || v <= todayIso(), "Cannot be in the future"),
  order_id: z.string().optional(),
  conclusion: z.string().trim().max(2000).optional(),
  results: z.array(resultSchema),
});
type ReportValues = z.infer<typeof reportSchema>;

export function UploadReportDialog({
  patientId,
  openOrders,
  open,
  onClose,
}: {
  patientId: string;
  openOrders: TestOrder[];
  open: boolean;
  onClose: () => void;
}) {
  const record = useRecordReport(patientId);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const { register, control, handleSubmit, reset, setError, formState } = useForm<ReportValues>({
    resolver: zodResolver(reportSchema),
    defaultValues: { results: [] },
  });
  const results = useFieldArray({ control, name: "results" });
  const { run, formError, clearFormError } = useSubmit(setError);

  const close = () => {
    reset();
    setFile(null);
    setFileError(null);
    clearFormError();
    onClose();
  };

  const pickFile = (f: File | null) => {
    setFileError(null);
    if (f && !ACCEPT.split(",").includes(f.type)) {
      setFileError("Upload a PDF or an image (JPEG, PNG, WebP or HEIC).");
      return setFile(null);
    }
    if (f && f.size > MAX_BYTES) {
      setFileError("Files must be smaller than 15 MB.");
      return setFile(null);
    }
    setFile(f);
  };

  const onSubmit = handleSubmit(async (v) => {
    if (!file && v.results.length === 0) {
      setFileError("Attach the report file or enter at least one value.");
      return;
    }
    const ok = await run(async () => {
      const doc = file
        ? await uploadDocument(patientId, file, {
            document_type: "lab_report",
            title: v.lab_name ? `Report from ${v.lab_name}` : undefined,
            document_date: v.collected_on || undefined,
          })
        : null;
      await record.mutateAsync({
        document_id: doc?.id ?? null,
        order_id: v.order_id || null,
        lab_name: v.lab_name || null,
        collected_at: v.collected_on ? new Date(`${v.collected_on}T00:00:00`).toISOString() : null,
        conclusion: v.conclusion || null,
        results: v.results.map((r) => {
          const n = Number(r.value);
          const numeric = r.value !== "" && Number.isFinite(n);
          return {
            analyte_name: r.analyte_name,
            value_numeric: numeric ? r.value : null,
            value_text: numeric ? null : r.value,
            unit: r.unit || null,
            reference_text: r.reference_text || null,
            flag: r.flag,
          };
        }),
      });
    }, "Report recorded");
    if (ok) close();
  });

  const e = formState.errors;
  return (
    <Dialog
      open={open}
      onClose={close}
      size="lg"
      title="Upload a report"
      description="Attach the report and, optionally, enter values exactly as printed. You are recorded as the verifier."
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" form="upload-report" loading={formState.isSubmitting}>
            Save report
          </Button>
        </>
      }
    >
      <form id="upload-report" onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        {formError && <Alert tone="danger">{formError}</Alert>}
        <div>
          <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-line px-4 py-6 text-center hover:bg-surface-2">
            <Upload className="size-6 text-muted" aria-hidden />
            <span className="text-sm font-medium">{file ? file.name : "Choose a PDF or image"}</span>
            <span className="text-xs text-muted">{file ? bytes(file.size) : "Up to 15 MB"}</span>
            <input
              type="file"
              accept={ACCEPT}
              className="sr-only"
              onChange={(ev) => pickFile(ev.target.files?.[0] ?? null)}
            />
          </label>
          {fileError && (
            <p className="mt-2 text-sm text-danger" role="alert">
              {fileError}
            </p>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Laboratory" error={e.lab_name?.message}>
            {(p) => <Input {...p} {...register("lab_name")} />}
          </Field>
          <Field label="Sample collected on" error={e.collected_on?.message}>
            {(p) => <Input {...p} type="date" max={todayIso()} {...register("collected_on")} />}
          </Field>
          <Field label="For order">
            {(p) => (
              <Select {...p} {...register("order_id")}>
                <option value="">Not linked</option>
                {openOrders.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.tests.slice(0, 3).join(", ")}
                    {o.tests.length > 3 ? "…" : ""}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-1 text-sm font-semibold">Values (optional, as printed)</legend>
          {results.fields.length === 0 && (
            <p className="text-sm text-muted">No values entered. The attached file will be the record.</p>
          )}
          {results.fields.map((field, i) => (
            <div key={field.id} className="grid grid-cols-2 gap-2 rounded-lg border border-line p-3 sm:grid-cols-6">
              <Input aria-label="Test name" placeholder="Test" className="col-span-2" {...register(`results.${i}.analyte_name`)} />
              <Input aria-label="Value" placeholder="Value" {...register(`results.${i}.value`)} />
              <Input aria-label="Unit" placeholder="Unit" {...register(`results.${i}.unit`)} />
              <Input aria-label="Reference range" placeholder="Ref. range" {...register(`results.${i}.reference_text`)} />
              <div className="flex gap-2">
                <Select aria-label="Flag as printed" {...register(`results.${i}.flag`)}>
                  <option value="unknown">No flag</option>
                  <option value="normal">Normal</option>
                  <option value="low">Low</option>
                  <option value="high">High</option>
                  <option value="critical_low">Critical low</option>
                  <option value="critical_high">Critical high</option>
                  <option value="abnormal">Abnormal</option>
                </Select>
                <Button variant="ghost" size="sm" aria-label="Remove value" onClick={() => results.remove(i)}>
                  <Trash2 className="size-4" />
                </Button>
              </div>
              {e.results?.[i] && (
                <p className="col-span-full text-sm text-danger" role="alert">
                  Each value needs a test name and a value.
                </p>
              )}
            </div>
          ))}
          <div>
            <Button
              variant="secondary"
              size="sm"
              icon={<Plus className="size-4" />}
              onClick={() => results.append({ analyte_name: "", value: "", unit: "", reference_text: "", flag: "unknown" })}
            >
              Add a value
            </Button>
          </div>
        </fieldset>

        <Field label="Conclusion (as written on the report)" error={e.conclusion?.message}>
          {(p) => <Textarea {...p} rows={2} {...register("conclusion")} />}
        </Field>
      </form>
    </Dialog>
  );
}
