import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";

import type { ReportDetail } from "./api";
import { PatientUploadReportDialog } from "./PatientUploadReportDialog";
import { ReportDetailDialog } from "./ReportDetailDialog";

const upload = vi.fn();
const review = vi.fn();
const inError = vi.fn();
const share = vi.fn();
const revoke = vi.fn();
let detail: ReportDetail;

const mutation = (fn: ReturnType<typeof vi.fn>) => ({ mutateAsync: fn, isPending: false });

vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  useReport: () => ({ data: detail, isPending: false, isError: false, isSuccess: true, status: "success" }),
  useUploadReport: () => mutation(upload),
  useReviewReport: () => mutation(review),
  useReportInError: () => mutation(inError),
  useShareReport: () => mutation(share),
  useRevokeShare: () => mutation(revoke),
}));

function wrap(ui: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>,
  );
}

function report(p: Partial<ReportDetail> = {}): ReportDetail {
  return {
    id: "r1",
    status: "pending_review",
    source: "patient",
    test_name: "Placeholder test",
    report_date: "2026-09-01",
    lab_name: "Placeholder lab",
    lab_reference: "REF-1",
    notes: "Placeholder note",
    collected_at: null,
    reported_at: null,
    conclusion: null,
    document_id: "d1",
    file: { id: "d1", content_type: "application/pdf", size_bytes: 2048, scan_status: "clean", available: true },
    order_id: "o1",
    ordering_doctor_name: "Dr Placeholder A",
    verified_at: null,
    reviewed_at: null,
    review_note: null,
    created_at: "2026-09-02T10:00:00Z",
    results: [],
    access: "full",
    shares: [],
    share_targets: [{ doctor_id: "doc-b", name: "Dr Placeholder B", specialty: "Specialty B" }],
    ...p,
  };
}

const file = (name: string, type: string, size = 1000) => new File([new Uint8Array(size)], name, { type });

describe("PatientUploadReportDialog", () => {
  beforeEach(() => upload.mockReset().mockResolvedValue({}));

  it("accepts only PDF, JPG and PNG", async () => {
    wrap(<PatientUploadReportDialog patientId="p1" orders={[]} onClose={() => {}} />);
    await userEvent.upload(screen.getByLabelText("Report file"), file("scan.webp", "image/webp"), { applyAccept: false });
    expect(screen.getByRole("alert")).toHaveTextContent("PDF, JPG or PNG");
    await userEvent.click(screen.getByRole("button", { name: "Upload report" }));
    expect(upload).not.toHaveBeenCalled();
  });

  it("rejects files over 15 MB", async () => {
    wrap(<PatientUploadReportDialog patientId="p1" orders={[]} onClose={() => {}} />);
    await userEvent.upload(screen.getByLabelText("Report file"), file("big.pdf", "application/pdf", 15 * 1024 * 1024 + 1));
    expect(screen.getByRole("alert")).toHaveTextContent("smaller than 15 MB");
  });

  it("uploads the file with its details", async () => {
    const onClose = vi.fn();
    wrap(<PatientUploadReportDialog patientId="p1" orders={[]} onClose={onClose} />);
    const pdf = file("report.pdf", "application/pdf");
    await userEvent.upload(screen.getByLabelText("Report file"), pdf);
    await userEvent.click(screen.getByRole("button", { name: "Upload report" }));
    expect(screen.getByRole("alert")).toHaveTextContent("name of the test");
    await userEvent.type(screen.getByLabelText(/Test name/), "Placeholder test");
    await userEvent.type(screen.getByLabelText("Date on the report"), "2026-09-01");
    await userEvent.click(screen.getByRole("button", { name: "Upload report" }));
    expect(upload).toHaveBeenCalledWith({
      file: pdf,
      order_id: null,
      test_name: "Placeholder test",
      report_date: "2026-09-01",
      lab_name: null,
      lab_reference: null,
      notes: null,
    });
    expect(onClose).toHaveBeenCalled();
  });
});

describe("ReportDetailDialog", () => {
  beforeEach(() => {
    for (const fn of [review, inError, share, revoke]) fn.mockReset().mockResolvedValue({});
    detail = report();
  });

  it("patient: shows the details, never interprets, and can share with one doctor", async () => {
    wrap(<ReportDetailDialog patientId="p1" reportId="r1" viewer="patient" self canWithdraw onClose={() => {}} />);
    expect(screen.getByText("Uploaded by you")).toBeInTheDocument();
    expect(screen.getByText("Dr Placeholder A")).toBeInTheDocument();
    expect(screen.getByText("REF-1")).toBeInTheDocument();
    expect(screen.getByText(/never interprets them/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Share with"), "doc-b");
    await userEvent.click(screen.getByRole("button", { name: "Share" }));
    expect(share).toHaveBeenCalledWith({ report_id: "r1", doctor_id: "doc-b", expires_at: null });
  });

  it("patient: withdrawing an upload needs a reason", async () => {
    wrap(<ReportDetailDialog patientId="p1" reportId="r1" viewer="patient" self canWithdraw onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Withdraw this upload" }));
    await userEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    expect(inError).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText(/Why are you withdrawing/), "wrong file");
    await userEvent.click(screen.getByRole("button", { name: "Withdraw" }));
    expect(inError).toHaveBeenCalledWith({ report_id: "r1", reason: "wrong file" });
  });

  it("patient: can stop sharing", async () => {
    detail = report({ shares: [{ id: "s1", doctor_id: "doc-b", doctor_name: "Dr Placeholder B", expires_at: null, shared_at: "2026-09-02T10:00:00Z" }] });
    wrap(<ReportDetailDialog patientId="p1" reportId="r1" viewer="patient" self onClose={() => {}} />);
    const item = screen.getByText("Dr Placeholder B").closest("li")!;
    await userEvent.click(within(item).getByRole("button", { name: "Stop sharing" }));
    expect(revoke).toHaveBeenCalledWith("s1");
  });

  it("doctor: verifies an uploaded report; rejecting needs a reason", async () => {
    detail = report({ shares: null, share_targets: null });
    wrap(<ReportDetailDialog patientId="p1" reportId="r1" viewer="doctor" canReview onClose={() => {}} />);
    expect(screen.queryByText("Sharing")).not.toBeInTheDocument();
    expect(screen.getByText(/not an interpretation of the results/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Verify" }));
    expect(review).toHaveBeenCalledWith({ report_id: "r1", decision: "verify" });
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    await userEvent.click(screen.getByRole("button", { name: "Reject report" }));
    expect(review).toHaveBeenCalledTimes(1);
    await userEvent.type(screen.getByLabelText(/not accepted/), "not this patient");
    await userEvent.click(screen.getByRole("button", { name: "Reject report" }));
    expect(review).toHaveBeenLastCalledWith({ report_id: "r1", decision: "reject", note: "not this patient" });
  });

  it("doctor: a report shared one by one says so", () => {
    detail = report({ access: "shared", shares: null, share_targets: null, status: "verified" });
    wrap(<ReportDetailDialog patientId="p1" reportId="r1" viewer="doctor" onClose={() => {}} />);
    expect(screen.getByText(/shared this report with you/)).toBeInTheDocument();
  });
});
