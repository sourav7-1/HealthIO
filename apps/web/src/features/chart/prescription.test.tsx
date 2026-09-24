import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { PrescriptionDocument } from "./api";
import { PrescriptionPaper } from "./PrescriptionView";

function wrap(ui: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function doc(partial: Partial<PrescriptionDocument> = {}): PrescriptionDocument {
  return {
    id: "rx-2",
    revision: 1,
    status: "issued",
    prescribed_on: "2026-09-24",
    issued_at: "2026-09-24T10:00:00Z",
    valid_until: null,
    diagnosis_as_written: "Placeholder assessment",
    advice: "Placeholder advice",
    follow_up_on: "2026-10-08",
    follow_up_instructions: "Bring reports",
    revision_reason: null,
    supersedes_prescription_id: null,
    superseded_by_id: null,
    cancelled_at: null,
    cancel_reason: null,
    content_sha256: "a".repeat(64),
    prescriber: {
      name: "Dr Placeholder",
      qualifications: ["MBBS"],
      specialty: "General practice",
      registration_council: "Test Council",
      registration_number: "TEST-1",
      practice_name: null,
      practice_address: null,
    },
    patient: { name: "Placeholder Person", age_years: 40, sex: "female" },
    items: [
      {
        sequence: 1,
        medicine: "Medicine A",
        generic_name: "Generic A",
        strength: "500 mg",
        dosage_form: "tablet",
        route: null,
        dose: "1 tablet",
        frequency: "1-0-1",
        meal_relation: "after_food",
        duration_days: 5,
        is_prn: false,
        prn_reason: null,
        instructions: "Placeholder instruction",
      },
    ],
    versions: [],
    ...partial,
  };
}

describe("PrescriptionPaper", () => {
  it("shows every prescribed field as written", () => {
    wrap(<PrescriptionPaper doc={doc()} />);
    expect(screen.getByText("Reg. No. TEST-1 (Test Council)")).toBeInTheDocument();
    expect(screen.getByText("Generic: Generic A")).toBeInTheDocument();
    expect(screen.getByText("1 tablet · 1-0-1 · After food")).toBeInTheDocument();
    expect(screen.getByText("5 days")).toBeInTheDocument();
    expect(screen.getByText("Placeholder assessment")).toBeInTheDocument();
    expect(screen.getByText(/on or before/)).toBeInTheDocument();
    expect(screen.getByText("Placeholder Person · 40 y · Female")).toBeInTheDocument();
    expect(screen.queryByText("Superseded")).not.toBeInTheDocument();
  });

  it("marks an older version and never hides that it was corrected", () => {
    wrap(<PrescriptionPaper doc={doc({ status: "superseded" })} />);
    expect(screen.getByText("Superseded")).toBeInTheDocument();
  });

  it("says when patient details are not shared instead of leaving a gap", () => {
    wrap(<PrescriptionPaper doc={doc({ patient: null, revision: 2, revision_reason: "Strength corrected" })} />);
    expect(screen.getByText("details not shared with you")).toBeInTheDocument();
    expect(screen.getByText("Strength corrected")).toBeInTheDocument();
  });
});
