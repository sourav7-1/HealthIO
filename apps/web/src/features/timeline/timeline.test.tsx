import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";

import type { HistoryEntry, TimelineEvent, TimelineFilters, TimelinePage } from "./api";
import { TimelineView } from "./TimelineView";

const calls: TimelineFilters[] = [];
let page: TimelinePage;
let history: HistoryEntry[] = [];

vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  useTimeline: (_pid: string, filters: TimelineFilters) => {
    calls.push(filters);
    return { data: { pages: [page] }, isSuccess: true, isPending: false, isError: false, hasNextPage: false, status: "success" };
  },
  useSymptoms: () => ({ data: [] }),
  useRecordHistory: () => ({ data: { kind: "symptom", resource_id: "s1", entries: history }, isSuccess: true, isPending: false, isError: false, status: "success" }),
}));

const drA = { id: "d1", name: "Dr Placeholder A", specialty: "Specialty A" };

function event(p: Partial<TimelineEvent>): TimelineEvent {
  return {
    key: `${p.kind ?? "visit"}:${p.resource_id ?? "r"}`,
    at: "2026-08-10T09:30:00Z",
    date_only: false,
    kind: "visit",
    title: "Doctor visit (in person)",
    detail: null,
    status: "completed",
    resource_id: "r",
    visit_id: null,
    source: "doctor",
    doctor: drA,
    amended: false,
    has_history: false,
    ...p,
  };
}

function show(variant: "patient" | "doctor") {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <MemoryRouter>
          <TimelineView patientId="p1" variant={variant} self linkFor={() => null} />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("TimelineView", () => {
  beforeEach(() => {
    calls.length = 0;
    history = [];
    page = {
      items: [
        event({ kind: "symptom", resource_id: "s1", title: "Symptom reported: Placeholder ache", source: "patient", doctor: null, amended: true, has_history: true, at: "2026-09-02T08:00:00Z", status: "ongoing" }),
        event({ kind: "visit", resource_id: "v1", visit_id: "v1" }),
        event({ kind: "document", resource_id: "doc1", title: "Document added: Placeholder card", date_only: true, at: "2025-03-01T12:00:00Z", source: "patient", doctor: null, status: "clean" }),
      ],
      next_cursor: null,
      total: 3,
      facets: {
        kinds: [
          { value: "document", count: 1 },
          { value: "symptom", count: 1 },
          { value: "visit", count: 1 },
        ],
        doctors: [{ ...drA, count: 1 }],
        specialties: [{ value: "Specialty A", count: 1 }],
        earliest: "2025-03-01T12:00:00Z",
        latest: "2026-09-02T08:00:00Z",
      },
    };
  });

  it("patient view: plain words, grouped by month, corrections marked", () => {
    show("patient");
    expect(screen.getByRole("heading", { name: /September 2026/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /March 2025/ })).toBeInTheDocument();
    expect(screen.getByText("You reported: Placeholder ache")).toBeInTheDocument();
    expect(screen.getByText("You saw Dr Placeholder A")).toBeInTheDocument();
    expect(screen.getByText("Corrected")).toBeInTheDocument();
    expect(screen.getByText("Specialty A")).toBeInTheDocument();
    expect(screen.getByText("3 records")).toBeInTheDocument();
  });

  it("doctor view: record type, author and specialty on each row", () => {
    show("doctor");
    const row = screen.getByText("Doctor visit (in person)").closest("li")!;
    expect(within(row).getByText("Dr Placeholder A · Specialty A")).toBeInTheDocument();
    expect(screen.getByText("Symptom reported: Placeholder ache")).toBeInTheDocument();
    expect(screen.getByText("Amended")).toBeInTheDocument();
  });

  it("filters by record type, doctor, specialty and date on the server", async () => {
    show("doctor");
    await userEvent.click(screen.getByRole("button", { name: /Symptoms/ }));
    expect(calls.at(-1)?.kinds).toEqual(["symptom"]);
    await userEvent.click(screen.getByRole("button", { name: /Filters/ }));
    await userEvent.selectOptions(screen.getByLabelText("Doctor"), "d1");
    await userEvent.selectOptions(screen.getByLabelText("Specialty"), "Specialty A");
    await userEvent.type(screen.getByLabelText("From"), "2026-01-01");
    const last = calls.at(-1)!;
    expect(last).toMatchObject({ kinds: ["symptom"], doctorId: "d1", specialty: "Specialty A", dateFrom: "2026-01-01" });
    await userEvent.click(screen.getByRole("button", { name: "Newest first" }));
    expect(calls.at(-1)?.order).toBe("oldest");
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(calls.at(-1)).toMatchObject({ kinds: [], doctorId: "", specialty: "", dateFrom: "" });
  });

  it("shows who changed a record, what and why", async () => {
    history = [
      { at: "2026-09-02T08:00:00Z", action: "created", actor: { role: "patient", name: null }, reason: null, version: 1, changes: [] },
      {
        at: "2026-09-03T08:00:00Z",
        action: "changed",
        actor: { role: "patient", name: null },
        reason: "picked the wrong level",
        version: 2,
        changes: [{ field: "severity", before: "mild", after: "moderate" }],
      },
    ];
    show("patient");
    await userEvent.click(screen.getByRole("button", { name: /History of/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Reason:", { exact: false })).toBeInTheDocument();
    expect(within(dialog).getByText("picked the wrong level")).toBeInTheDocument();
    expect(within(dialog).getByText("mild")).toBeInTheDocument();
    expect(within(dialog).getByText("moderate")).toBeInTheDocument();
    expect(within(dialog).getAllByText(/You ·/)).toHaveLength(2);
  });

  it("explains an empty filtered result", () => {
    page = { ...page, items: [], total: 0 };
    show("patient");
    expect(screen.getByText("Nothing recorded yet")).toBeInTheDocument();
  });
});
