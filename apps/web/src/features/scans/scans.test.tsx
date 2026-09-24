import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";

import type { Scan, ScanField } from "./api";
import { FieldRow, ReviewBody } from "./ScanReviewPage";

vi.mock("@/features/patient/context", () => ({
  useActivePatient: () => ({ patientId: "p1", base: "/patient", mode: "self", can: () => true }),
}));

function wrap(ui: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

function field(partial: Partial<ScanField> & { band?: "high" | "medium" | "low" | "absent"; aiValue?: string | null }): ScanField {
  const { band = "high", aiValue = "500 mg", ...rest } = partial;
  return {
    ai: {
      value: aiValue,
      confidence: band === "high" ? 0.97 : band === "medium" ? 0.75 : 0.4,
      model_confidence: 0.97,
      band,
      legibility: "clear",
      evidence: aiValue,
      region: null,
      region_source: null,
      flags: [],
      message: band === "low" ? "Could not confidently read this field." : null,
    },
    review: { status: "unverified", value: band === "low" || band === "absent" ? null : aiValue, at: null },
    critical: true,
    requires_confirmation: true,
    interpretation: null,
    ...rest,
  };
}

describe("FieldRow", () => {
  it("does not pre-fill a low-confidence reading and says so", async () => {
    const onOps = vi.fn().mockResolvedValue(undefined);
    wrap(<FieldRow label="Strength" field={field({ band: "low" })} itemKey="i1" name="strength" editable photo={null} busy={false} onOps={onOps} onFocus={() => {}} />);
    expect(screen.getByText("Could not confidently read this field.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Strength/)).toHaveValue("");
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();

    // The uncertain reading is only used if the person asks for it, then confirms it.
    await userEvent.click(screen.getByRole("button", { name: "Show what the AI thought it said" }));
    await userEvent.click(screen.getByRole("button", { name: "Put it in the box" }));
    expect(screen.getByLabelText(/Strength/)).toHaveValue("500 mg");
    expect(onOps).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onOps).toHaveBeenCalledWith([{ op: "set_field", item_key: "i1", field: "strength", action: "set", value: "500 mg" }]);
  });

  it("confirms a clear reading as is, or records a correction", async () => {
    const onOps = vi.fn().mockResolvedValue(undefined);
    wrap(<FieldRow label="Dose" field={field({ aiValue: "1 tab" })} itemKey="i1" name="dose" editable photo={null} busy={false} onOps={onOps} onFocus={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onOps).toHaveBeenLastCalledWith([{ op: "set_field", item_key: "i1", field: "dose", action: "confirm", value: null }]);
    await userEvent.click(screen.getByRole("button", { name: "Not on the prescription" }));
    expect(onOps).toHaveBeenLastCalledWith([{ op: "set_field", item_key: "i1", field: "dose", action: "not_on_prescription", value: null }]);
  });

  it("is read-only for someone who may not verify", () => {
    wrap(<FieldRow label="Dose" field={field({})} itemKey="i1" name="dose" editable={false} photo={null} busy={false} onOps={vi.fn()} onFocus={() => {}} />);
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });
});

describe("ReviewBody", () => {
  const scan = (issues: Scan["issues"]): Scan =>
    ({
      id: "s1",
      document_id: "d1",
      mode: "ai",
      status: "needs_review",
      created_at: "2026-09-24T10:00:00Z",
      prescription_id: null,
      error_message: null,
      error_code: null,
      is_prescription: true,
      handwritten: true,
      reading_notes: ["Bottom edge cut off"],
      discarded_keys: ["diagnosis"],
      model_metadata: {},
      header: Object.fromEntries(
        ["doctor_name", "doctor_registration", "clinic_name", "prescription_date"].map((f) => [f, field({ critical: false, requires_confirmation: false })]),
      ),
      items: [
        {
          key: "i1",
          from_ai: true,
          removed: false,
          fields: Object.fromEntries(
            ["medicine_name", "strength", "dose", "frequency", "duration", "meal_relation", "instructions"].map((f) => [f, field({})]),
          ),
        },
      ],
      issues,
      verifier_role: null,
      verification_level: null,
      verified_at: null,
      rejected_reason: null,
    }) as Scan;

  const props = { photo: null, focus: null, setFocus: () => {}, editable: true, busy: false, onOps: vi.fn(), onSave: vi.fn(), saving: false, onDiscard: vi.fn() };

  it("blocks saving until every flagged field is checked, and shows the warnings", () => {
    wrap(<ReviewBody scan={scan([{ path: "items.i1.strength", problem: "Confirm this" }])} {...props} />);
    expect(screen.getByRole("button", { name: "Save prescription" })).toBeDisabled();
    expect(screen.getByText(/still needs your check/)).toBeInTheDocument();
    expect(screen.getByText("Handwritten prescription")).toBeInTheDocument();
    expect(screen.getByText("Bottom edge cut off")).toBeInTheDocument();
    expect(screen.getByText(/does not collect \(such as a diagnosis\)/)).toBeInTheDocument();
    expect(screen.getByText(/Read by AI\. It can make mistakes/)).toBeInTheDocument();
  });

  it("allows saving once nothing is outstanding", () => {
    wrap(<ReviewBody scan={scan([])} {...props} />);
    expect(screen.getByRole("button", { name: "Save prescription" })).toBeEnabled();
  });
});
