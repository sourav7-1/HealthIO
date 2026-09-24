import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";

import type { Med } from "./api";
import { OriginBadge } from "./labels";
import { ScheduleDialog } from "./ScheduleDialog";
import { StopPauseDialog } from "./StopPauseDialog";

const saveSchedule = vi.fn();
const requestChange = vi.fn();
const stop = vi.fn();
const check = vi.fn();

vi.mock("@/features/patient/context", () => ({
  useActivePatient: () => ({ patientId: "p1", base: "/patient", mode: "self", can: () => true }),
  useMode: () => "self",
}));
vi.mock("./api", () => ({
  checkSchedule: (...args: unknown[]) => check(...args),
  useChangeSchedule: () => ({ mutateAsync: saveSchedule, isPending: false }),
  useRequestChange: () => ({ mutateAsync: requestChange, isPending: false }),
  useStop: () => ({ mutateAsync: stop, isPending: false }),
  usePause: () => ({ mutateAsync: vi.fn(), isPending: false }),
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

const med = {
  id: "m1",
  name: "Tab. Samplemycin",
  origin: "doctor_prescription",
  status: "active",
  is_prn: false,
  start_date: "2026-09-01",
  end_date: "2026-09-30",
  schedule: {
    type: "fixed_times",
    times_of_day: ["08:00:00", "20:00:00"],
    interval_minutes: null,
    pattern: { kind: "daily", every: 1, weekdays: [] },
    pattern_text: "every day",
    dose_amount: "1.000",
    dose_unit: "tablet",
    timezone: "Asia/Kolkata",
    meal_relation: "after_food",
    effective_from: "2026-09-01T00:00:00Z",
  },
} as unknown as Med;

beforeEach(() => {
  for (const fn of [saveSchedule, requestChange, stop, check]) fn.mockReset().mockResolvedValue({});
});

describe("OriginBadge", () => {
  it("names every source in words", () => {
    render(
      <>
        <OriginBadge origin="doctor_prescription" />
        <OriginBadge origin="uploaded_prescription_ai" />
        <OriginBadge origin="uploaded_prescription_typed" />
        <OriginBadge origin="self_reported" />
      </>,
    );
    expect(screen.getByText(/Prescribed by your doctor/)).toBeInTheDocument();
    expect(screen.getByText(/read by AI, checked/)).toBeInTheDocument();
    expect(screen.getByText(/typed in/)).toBeInTheDocument();
    expect(screen.getByText(/Added by you/)).toBeInTheDocument();
  });
});

describe("ScheduleDialog", () => {
  it("never saves a clinically relevant change without a clinician", async () => {
    check.mockResolvedValue({
      requires: "clinician",
      findings: [{ code: "dose_differs", message: "This dose is different from the prescription.", clinical: true }],
    });
    wrap(<ScheduleDialog med={med} onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Review change" }));
    expect(await screen.findByText("This dose is different from the prescription.")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save schedule" });
    expect(save).toBeDisabled();

    // Recording who advised it, and acknowledging, enables saving.
    await userEvent.click(screen.getByLabelText(/already told me/));
    await userEvent.type(screen.getByLabelText(/Their name/), "Placeholder Pharmacist");
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/differs from the prescription/));
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(saveSchedule).toHaveBeenCalledWith(
      expect.objectContaining({ acknowledged: true, advice: { role: "doctor", name: "Placeholder Pharmacist" } }),
    );
  });

  it("can send the change to the doctor instead", async () => {
    check.mockResolvedValue({ requires: "clinician", findings: [{ code: "frequency_differs", message: "x", clinical: true }] });
    wrap(<ScheduleDialog med={med} onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Review change" }));
    await userEvent.click(await screen.findByLabelText(/Ask my doctor/));
    await userEvent.click(screen.getByRole("button", { name: "Send to the doctor" }));
    expect(requestChange).toHaveBeenCalledWith(expect.objectContaining({ kind: "schedule" }));
    expect(saveSchedule).not.toHaveBeenCalled();
  });

  it("asks to acknowledge timing warnings", async () => {
    check.mockResolvedValue({ requires: "acknowledgement", findings: [{ code: "doses_close", message: "Two doses are less than 4 hours apart.", clinical: false }] });
    wrap(<ScheduleDialog med={med} onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Review change" }));
    const save = await screen.findByRole("button", { name: "Save schedule" });
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByLabelText("I have read these warnings"));
    expect(save).toBeEnabled();
  });
});

describe("StopPauseDialog", () => {
  it("defaults to asking the doctor for a prescribed medicine", async () => {
    wrap(<StopPauseDialog med={med} action="stop" onClose={() => {}} />);
    expect(screen.getByText("This medicine was prescribed")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ask the doctor" }));
    expect(requestChange).toHaveBeenCalledWith(expect.objectContaining({ kind: "stop" }));
    expect(stop).not.toHaveBeenCalled();
  });

  it("records the patient's own decision only after the warning is acknowledged", async () => {
    wrap(<StopPauseDialog med={med} action="stop" onClose={() => {}} />);
    await userEvent.click(screen.getByLabelText("It is my own decision"));
    const button = screen.getByRole("button", { name: "Stop" });
    expect(button).toBeDisabled();
    await userEvent.click(screen.getByLabelText("I understand the warning above"));
    await userEvent.click(button);
    expect(stop).toHaveBeenCalledWith(expect.objectContaining({ own_decision: true, acknowledged: true, advice: null }));
  });
});
