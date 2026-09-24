import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";

import type { Reminder } from "./api";
import { ReminderPrompt } from "./ReminderPrompt";

const mutateAsync = vi.fn();
let due: Reminder[] = [];

vi.mock("@/features/patient/context", () => ({
  useActivePatient: () => ({ patientId: "p1", base: "/patient", mode: "self", can: () => true }),
  useMode: () => "self",
}));
vi.mock("@/features/patient/api", () => ({
  useDoseAction: () => ({ mutateAsync, isPending: false }),
}));
vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  useDueReminders: () => ({ data: due }),
}));

function reminder(partial: Partial<Reminder> = {}): Reminder {
  return {
    dose_id: "d1",
    medication_id: "m1",
    medicine: "Tab. Samplemycin 500 mg",
    dose: "1 tablet",
    meal: "after_food",
    instructions: "With water",
    instructions_verified: true,
    source_label: "Prescribed by your doctor",
    origin: "doctor_prescription",
    scheduled_at: new Date().toISOString(),
    status: "notified",
    snooze_count: 0,
    can_snooze: true,
    default_snooze_minutes: 10,
    guidance: null,
    ...partial,
  };
}

function show(url = "/patient") {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <MemoryRouter initialEntries={[url]}>
          <ReminderPrompt />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("ReminderPrompt", () => {
  beforeEach(() => {
    mutateAsync.mockReset().mockResolvedValue({});
    due = [reminder()];
  });

  it("shows the medicine, dose, instructions and source exactly as recorded", () => {
    show();
    expect(screen.getByRole("heading", { name: "Time for your medication" })).toBeInTheDocument();
    expect(screen.getByText("Tab. Samplemycin 500 mg")).toBeInTheDocument();
    expect(screen.getByText("1 tablet")).toBeInTheDocument();
    expect(screen.getByText("After food · With water")).toBeInTheDocument();
    expect(screen.getByText("Instructions from the prescription")).toBeInTheDocument();
    expect(screen.getByText("Prescribed by your doctor", { exact: false })).toBeInTheDocument();
  });

  it("records Taken", async () => {
    show();
    await userEvent.click(screen.getByRole("button", { name: "Taken" }));
    expect(mutateAsync).toHaveBeenCalledWith({ dose_id: "d1", action: "take", reason: undefined, minutes: undefined });
  });

  it("snoozes for the person's chosen minutes", async () => {
    show();
    await userEvent.click(screen.getByRole("button", { name: "Snooze 10 min" }));
    expect(mutateAsync).toHaveBeenCalledWith({ dose_id: "d1", action: "snooze", reason: undefined, minutes: 10 });
  });

  it("hides Snooze once the snooze limit is reached", () => {
    due = [reminder({ can_snooze: false, snooze_count: 3, status: "snoozed" })];
    show();
    expect(screen.queryByRole("button", { name: /Snooze/ })).not.toBeInTheDocument();
    expect(screen.getByText(/no more snoozes/)).toBeInTheDocument();
  });

  it("skips with an optional reason", async () => {
    show();
    await userEvent.click(screen.getByRole("button", { name: "Skip" }));
    await userEvent.type(screen.getByLabelText("Reason (optional)"), "Out of stock");
    await userEvent.click(screen.getByRole("button", { name: "Mark as skipped" }));
    expect(mutateAsync).toHaveBeenCalledWith({ dose_id: "d1", action: "skip", reason: "Out of stock", minutes: undefined });
  });

  it("opens the skip step from a notification's Skip action, for that dose first", () => {
    due = [reminder({ dose_id: "d0", medicine: "Other medicine" }), reminder()];
    show("/patient?reminder=d1&action=skip");
    expect(screen.getByText("Tab. Samplemycin 500 mg")).toBeInTheDocument();
    expect(screen.getByLabelText("Reason (optional)")).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled(); // a notification tap never records a dose by itself
  });

  it("marks a patient's own note as not from a prescription", () => {
    due = [reminder({ instructions_verified: false, origin: "self_reported" })];
    show();
    expect(screen.getByText("Instructions (not from a prescription)")).toBeInTheDocument();
  });

  it("stays closed when nothing is due and after Close", async () => {
    show();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("Time for your medication")).not.toBeInTheDocument();
  });
});
