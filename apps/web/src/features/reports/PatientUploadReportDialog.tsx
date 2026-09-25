import { FileUp } from "lucide-react";
import { useState } from "react";

import { Alert, Button, Dialog, Field, Input, Select, Textarea, useToast } from "@/components/ui";
import type { TestOrder } from "@/features/chart/api";
import { errorMessage } from "@/lib/api";
import { bytes, formatDate, todayIso } from "@/lib/format";

import { REPORT_ACCEPT, checkReportFile, useUploadReport } from "./api";

/**
 * A patient or caregiver adds a test report: a PDF, JPG or PNG file with its details.
 * It is labelled as uploaded by them and waits for a doctor to review it.
 */
export function PatientUploadReportDialog({
  patientId,
  orders,
  onClose,
}: {
  patientId: string;
  /** Open orders the report can be linked to (its test name then comes from the order). */
  orders: TestOrder[];
  onClose: () => void;
}) {
  const upload = useUploadReport(patientId);
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [orderId, setOrderId] = useState("");
  const [testName, setTestName] = useState("");
  const [date, setDate] = useState("");
  const [lab, setLab] = useState("");
  const [reference, setReference] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  const pick = (f: File | null) => {
    setError(null);
    if (f) {
      const problem = checkReportFile(f);
      if (problem) {
        setFile(null);
        return setError(problem);
      }
    }
    setFile(f);
  };

  const save = async () => {
    if (!file) return setError("Choose the report file.");
    if (!orderId && !testName.trim()) return setError("Add the name of the test, or choose the test your doctor ordered.");
    setError(null);
    try {
      await upload.mutateAsync({
        file,
        order_id: orderId || null,
        test_name: testName.trim() || null,
        report_date: date || null,
        lab_name: lab.trim() || null,
        lab_reference: reference.trim() || null,
        notes: notes.trim() || null,
      });
      toast.success("Report added. Your doctor will review it.");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Upload a test report"
      description="Stored privately and checked for viruses. It is marked as uploaded by you until a doctor reviews it."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={() => void save()} loading={upload.isPending}>Upload report</Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-line px-4 py-6 text-center hover:bg-surface-2 focus-within:ring-2 focus-within:ring-accent">
          <FileUp className="size-6 text-muted" aria-hidden />
          <span className="text-sm font-medium">{file ? file.name : "Choose a PDF, JPG or PNG"}</span>
          <span className="text-xs text-muted">{file ? bytes(file.size) : "Up to 15 MB"}</span>
          <input type="file" accept={REPORT_ACCEPT} className="sr-only" aria-label="Report file" onChange={(e) => pick(e.target.files?.[0] ?? null)} />
        </label>
        {orders.length > 0 && (
          <Field label="Which test is this for?">
            {(p) => (
              <Select {...p} value={orderId} onChange={(e) => setOrderId(e.target.value)}>
                <option value="">Not one my doctor ordered here</option>
                {orders.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.tests.join(", ")} · ordered {formatDate(o.ordered_at.slice(0, 10))}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}
        <Field label="Test name" required={!orderId} hint={orderId ? "Leave empty to use the name of the ordered test." : undefined}>
          {(p) => <Input {...p} value={testName} maxLength={200} onChange={(e) => setTestName(e.target.value)} />}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Date on the report">{(p) => <Input {...p} type="date" max={todayIso()} value={date} onChange={(e) => setDate(e.target.value)} />}</Field>
          <Field label="Laboratory (optional)">{(p) => <Input {...p} value={lab} maxLength={200} onChange={(e) => setLab(e.target.value)} />}</Field>
          <Field label="Report number (optional)">{(p) => <Input {...p} value={reference} maxLength={100} onChange={(e) => setReference(e.target.value)} />}</Field>
        </div>
        <Field label="Notes (optional)" hint="For example, where you had the test. Don't add results here; they are in the file.">
          {(p) => <Textarea {...p} rows={2} value={notes} maxLength={2000} onChange={(e) => setNotes(e.target.value)} />}
        </Field>
      </div>
    </Dialog>
  );
}
