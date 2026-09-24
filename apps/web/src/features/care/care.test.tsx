import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui";
import { ModeProvider } from "@/features/patient/context";

import type { PersonSummary } from "./api";
import { PersonCard } from "./pages";

function wrap(ui: ReactNode) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter>
          <ModeProvider mode="caregiver">{ui}</ModeProvider>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const dose = {
  id: "d1",
  medication_id: "m1",
  medication_name: "Test medicine",
  strength: null,
  instructions: null,
  meal_relation: null,
  source: "prescription",
  scheduled_at: new Date(Date.now() + 3_600_000).toISOString(),
  status: "scheduled",
  snoozed_until: null,
  snooze_count: 0,
  taken_at: null,
  skip_reason: null,
  as_needed: false,
} as PersonSummary["today_doses"] extends (infer T)[] | null ? T : never;

function person(partial: Partial<PersonSummary>): PersonSummary {
  return {
    patient_id: "p1",
    relationship_id: "r1",
    name: "Placeholder Person",
    relationship_type: "child",
    is_guardian: false,
    is_dependant: false,
    permissions: [],
    today_doses: null,
    missed_doses: null,
    appointments: null,
    follow_ups: null,
    recent_reports: null,
    ...partial,
  };
}

describe("PersonCard", () => {
  it("says a section is not shared instead of showing it as empty", () => {
    wrap(<PersonCard person={person({ appointments: [], follow_ups: [] })} />);
    expect(screen.getAllByText("Not shared with you")).toHaveLength(3); // medicines, missed, reports
    expect(screen.getByText("Nothing upcoming.")).toBeInTheDocument();
  });

  it("lets a caregiver with log_doses act on a dose, in the caregiver's voice", () => {
    wrap(<PersonCard person={person({ permissions: ["view_medications", "log_doses"], today_doses: [dose], missed_doses: [] })} />);
    const card = screen.getByRole("article");
    expect(within(card).getByRole("button", { name: "Taken" })).toBeInTheDocument();
    expect(within(card).getByText("Prescribed by a doctor")).toBeInTheDocument();
  });

  it("shows doses read-only without log_doses", () => {
    wrap(<PersonCard person={person({ permissions: ["view_medications"], today_doses: [dose], missed_doses: [] })} />);
    expect(screen.queryByRole("button", { name: "Taken" })).not.toBeInTheDocument();
    expect(screen.getByText("Test medicine")).toBeInTheDocument();
  });
});
