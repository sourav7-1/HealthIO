import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";

import type { SafetyCheck, SafetyWarning } from "./api";
import { IssueWithSafetyCheck, SafetyWarningsCard } from "./SafetyWarnings";

const review = vi.fn();
let warnings: SafetyWarning[] = [];
let datasets: { name: string; version: string }[] = [];
let check: SafetyCheck | undefined;

vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  useSafetyWarnings: () => ({ data: warnings, isSuccess: true }),
  useReferenceDatasets: () => ({ data: datasets }),
  useReviewWarning: () => ({ mutateAsync: review, isPending: false }),
  useRecheck: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePrescriptionSafetyCheck: () => ({ data: check, isPending: false, isError: false }),
}));

function wrap(ui: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>,
  );
}

const warning = (p: Partial<SafetyWarning> = {}): SafetyWarning => ({
  id: "w1",
  headline: "Potential issue detected. Please confirm with a doctor/pharmacist.",
  kind: "interaction",
  severity: "serious",
  source_severity: "major",
  title: "Possible interaction: Placeholder A and Placeholder B",
  detail: "Placeholder Set lists a possible interaction. Please don't stop, skip or change any medicine on your own because of this.",
  source_type: "reference_dataset",
  source_name: "Placeholder Set",
  source_version: "2026.09",
  subjects: ["med:1", "med:2"],
  status: "open",
  detected_at: "2026-09-27T10:00:00Z",
  last_checked_at: "2026-09-27T10:00:00Z",
  resolved_at: null,
  review_status: "unreviewed",
  reviewed_at: null,
  reviewer_role: null,
  review_note: null,
  ...p,
});

describe("SafetyWarningsCard", () => {
  beforeEach(() => {
    review.mockReset().mockResolvedValue({});
    warnings = [warning()];
    datasets = [{ name: "Placeholder Set", version: "2026.09" }];
  });

  it("patient: shows the fixed headline, source, severity and lets them acknowledge", async () => {
    wrap(<SafetyWarningsCard patientId="p1" viewer="patient" />);
    expect(screen.getByText("Potential issue detected. Please confirm with a doctor/pharmacist.")).toBeInTheDocument();
    expect(screen.getByText(/Please don't stop, skip or change any medicine on your own because of this\.$/, { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Important: check soon")).toBeInTheDocument();
    expect(screen.getByText(/Source: Placeholder Set \(2026\.09\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "I've seen this" }));
    expect(review).toHaveBeenCalledWith({ warning_id: "w1", note: undefined });
  });

  it("doctor: marking reviewed needs a note", async () => {
    wrap(<SafetyWarningsCard patientId="p1" viewer="doctor" />);
    await userEvent.click(screen.getByRole("button", { name: "Mark reviewed" }));
    const save = screen.getByRole("button", { name: "Save review" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Review note/), "Aware, monitoring");
    await userEvent.click(save);
    expect(review).toHaveBeenCalledWith({ warning_id: "w1", note: "Aware, monitoring" });
  });

  it("says plainly when interaction data is not loaded", () => {
    warnings = [];
    datasets = [];
    wrap(<SafetyWarningsCard patientId="p1" viewer="patient" />);
    expect(screen.getByText(/No potential issues found by the available checks/)).toBeInTheDocument();
    expect(screen.getByText(/No interaction database is loaded yet/)).toBeInTheDocument();
  });

  it("shows who reviewed a warning", () => {
    warnings = [warning({ review_status: "reviewed", reviewed_at: "2026-09-27T11:00:00Z", review_note: "Aware" })];
    wrap(<SafetyWarningsCard patientId="p1" viewer="patient" />);
    expect(screen.getByText(/Reviewed by a doctor/)).toHaveTextContent("Aware");
    expect(screen.queryByRole("button", { name: "I've seen this" })).not.toBeInTheDocument();
  });
});

describe("IssueWithSafetyCheck", () => {
  it("shows potential issues before issuing, and issues on confirm", async () => {
    check = {
      findings: [
        {
          headline: "Potential issue detected. Please confirm with a doctor/pharmacist.",
          kind: "inconsistent_prescription",
          severity: "caution",
          source_severity: null,
          title: "Prescription details for Placeholder B don't match",
          detail: "Placeholder B: the frequency is written as '1-0-1' (2 a day) but recorded as 3 a day.",
          source_name: "Health Io prescription consistency rules",
          source_version: "1",
          subjects: ["rxi:1", "rx:1"],
        },
      ],
      unknown_ingredients: ["Placeholder B"],
      datasets: [],
    };
    const onIssue = vi.fn().mockResolvedValue(undefined);
    wrap(<IssueWithSafetyCheck patientId="p1" prescriptionId="rx1" open description="…" onIssue={onIssue} onClose={() => {}} />);
    expect(screen.getByText(/written as '1-0-1'/)).toBeInTheDocument();
    expect(screen.getByText(/Ingredients not known for: Placeholder B/)).toBeInTheDocument();
    expect(screen.getByText(/interactions and condition cautions were not checked/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Issue anyway" }));
    expect(onIssue).toHaveBeenCalled();
  });
});
