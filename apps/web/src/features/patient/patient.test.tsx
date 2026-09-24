import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";

import type { Dose } from "./api";
import { DoseCard, SourceBadge } from "./components";

const mutateAsync = vi.fn();
vi.mock("./api", () => ({
  OPEN_DOSE: ["scheduled", "notified", "snoozed"],
  useDoseAction: () => ({ mutateAsync, isPending: false }),
}));

function wrap(ui: ReactNode) {
  const qc = new QueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>,
  );
}

function dose(partial: Partial<Dose>): Dose {
  return {
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
    ...partial,
  } as Dose;
}

describe("SourceBadge", () => {
  it("labels doctor-prescribed and self-reported medicines in words", () => {
    render(
      <>
        <SourceBadge source="prescription" />
        <SourceBadge source="self_reported" />
      </>,
    );
    expect(screen.getByText("Prescribed by your doctor")).toBeInTheDocument();
    expect(screen.getByText("Added by you or your caregiver")).toBeInTheDocument();
  });
});

describe("DoseCard", () => {
  beforeEach(() => mutateAsync.mockReset().mockResolvedValue({}));

  it("confirms a dose as taken", async () => {
    wrap(<DoseCard dose={dose({})} patientId="p1" />);
    await userEvent.click(screen.getByRole("button", { name: "Taken" }));
    expect(mutateAsync).toHaveBeenCalledWith({ dose_id: "d1", action: "take" });
  });

  it("asks before skipping and sends the optional reason", async () => {
    wrap(<DoseCard dose={dose({})} patientId="p1" />);
    await userEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(mutateAsync).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText("Reason (optional)"), "Felt sick");
    await userEvent.click(screen.getByRole("button", { name: "Mark as skipped" }));
    expect(mutateAsync).toHaveBeenCalledWith({ dose_id: "d1", action: "skip", reason: "Felt sick" });
  });

  it("stops offering snooze after the limit", () => {
    wrap(<DoseCard dose={dose({ status: "snoozed", snooze_count: 3 })} patientId="p1" />);
    expect(screen.queryByRole("button", { name: "Snooze" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Taken" })).toBeInTheDocument();
  });

  it("shows no actions to someone who may only view doses", () => {
    wrap(<DoseCard dose={dose({})} patientId="p1" canAct={false} />);
    expect(screen.queryByRole("button", { name: "Taken" })).not.toBeInTheDocument();
    expect(screen.getByText("To take")).toBeInTheDocument();
  });

  it("shows no actions for a dose already taken", () => {
    wrap(<DoseCard dose={dose({ status: "taken", taken_at: new Date().toISOString() })} patientId="p1" />);
    expect(screen.queryByRole("button", { name: "Taken" })).not.toBeInTheDocument();
    expect(screen.getByText(/Taken/)).toBeInTheDocument();
  });
});

describe("DoseCard for a missed dose", () => {
  it("shows the prescription's words and points to a doctor or pharmacist", () => {
    wrap(
      <DoseCard
        dose={dose({ status: "missed" })}
        patientId="p1"
        guidance={{
          instructions_as_written: "After food",
          instructions_verified: true,
          message: "Ask your doctor or pharmacist.",
          source_label: "Prescribed by your doctor",
        }}
      />,
    );
    expect(screen.getByText("What the prescription says:", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("After food")).toBeInTheDocument();
    expect(screen.getByText("Ask your doctor or pharmacist.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "I took it" })).toBeInTheDocument();
  });

  it("treats a notified dose as still to take", () => {
    wrap(<DoseCard dose={dose({ status: "notified" })} patientId="p1" />);
    expect(screen.getByRole("button", { name: "Taken" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Snooze" })).toBeInTheDocument();
  });
});
