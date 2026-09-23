import { QueryClient, QueryClientProvider, type UseQueryResult } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui";
import { ApiError } from "@/lib/api";

import { AddPatientDialog } from "./forms/AddPatientDialog";
import { PatientName, QueryState } from "./shared";

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter>{ui}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

function query<T>(partial: Partial<UseQueryResult<T>>): UseQueryResult<T> {
  return { isPending: false, isError: false, data: undefined, error: null, refetch: async () => ({}) as never, ...partial } as UseQueryResult<T>;
}

describe("QueryState", () => {
  it("explains consent when the API answers 403", () => {
    wrap(
      <QueryState query={query<string[]>({ isError: true, error: new ApiError(403, { type: "x/forbidden", title: "Access denied", status: 403 }) })} what="Visits">
        {() => <p>data</p>}
      </QueryState>,
    );
    expect(screen.getByText("Visits not shared with you")).toBeInTheDocument();
    expect(screen.queryByText("data")).not.toBeInTheDocument();
  });

  it("shows the empty state instead of an empty list", () => {
    wrap(
      <QueryState query={query<string[]>({ data: [] })} what="Visits" isEmpty={(d) => d.length === 0} empty={<p>No visits recorded</p>}>
        {() => <p>data</p>}
      </QueryState>,
    );
    expect(screen.getByText("No visits recorded")).toBeInTheDocument();
  });

  it("shows a loading state while pending", () => {
    wrap(
      <QueryState query={query<string[]>({ isPending: true })} what="Visits">
        {() => <p>data</p>}
      </QueryState>,
    );
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });
});

describe("PatientName", () => {
  it("never invents a name when demographics are not shared", () => {
    wrap(<PatientName name={null} />);
    expect(screen.getByText("Name not shared")).toBeInTheDocument();
  });
});

describe("AddPatientDialog", () => {
  it("requires the doctor to confirm the patient's consent", async () => {
    wrap(<AddPatientDialog open onClose={() => {}} />);
    await userEvent.type(screen.getByLabelText(/First name/), "Placeholder");
    await userEvent.click(screen.getByRole("button", { name: "Add patient" }));
    expect(await screen.findByText("Confirm the patient agreed before continuing")).toBeInTheDocument();
  });
});
