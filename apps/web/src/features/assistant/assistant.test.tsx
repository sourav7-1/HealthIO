import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/ui";

import { AnswerCard } from "./AnswerCard";
import type { Answer } from "./api";
import { AssistantPage } from "./AssistantPage";

const ask = vi.fn();

vi.mock("@/features/patient/context", () => ({
  useActivePatient: () => ({ patientId: "p1", base: "/patient", mode: "self", name: null, can: () => true }),
}));
vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  useAssistantPreferences: () => ({ data: { history_days: 30, save_by_default: true, use_records: true, history_day_choices: [1, 7, 30, 90] } }),
  useUpdateAssistantPreferences: () => ({ mutateAsync: vi.fn() }),
  useConversations: () => ({ data: [] }),
  useConversation: () => ({ data: undefined }),
  useDeleteConversation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAsk: () => ({ mutateAsync: ask, isPending: false }),
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

const answer = (p: Partial<Answer> = {}): Answer => ({
  segments: [
    {
      kind: "record",
      text: "You take Placeholder A 500 mg at 08:00.",
      sources: [{ id: "med:1", kind: "record", label: "Medication: Placeholder A", publisher: null, url: null, reviewed_on: null }],
    },
    {
      kind: "general",
      text: "Store medicines away from heat.",
      sources: [
        { id: "lib:1", kind: "library", label: "Storing medicines", publisher: "MedlinePlus (U.S. National Library of Medicine)", url: "https://medlineplus.gov/placeholder.html", reviewed_on: "2026-09-01" },
      ],
    },
    { kind: "uncertain", text: "I don't know if it interacts with food.", sources: [] },
  ],
  urgent: false,
  emergency: [],
  declined: null,
  questions_for_doctor: ["Can I take it with milk?"],
  mode: "ai",
  disclaimer: "…",
  ...p,
});

describe("AnswerCard", () => {
  it("labels where each part comes from, with its sources", () => {
    wrap(<AnswerCard answer={answer()} />);
    const record = screen.getByRole("region", { name: "From the record" });
    expect(within(record).getByText("Medication: Placeholder A")).toBeInTheDocument();
    const general = screen.getByRole("region", { name: "General information" });
    const link = within(general).getByRole("link", { name: /MedlinePlus/ });
    expect(link).toHaveAttribute("href", "https://medlineplus.gov/placeholder.html");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("region", { name: "Not sure" })).toHaveTextContent("I don't know");
    expect(screen.getByText("Can I take it with milk?")).toBeInTheDocument();
  });

  it("puts emergency guidance first with call buttons", () => {
    wrap(<AnswerCard answer={answer({ urgent: true, emergency: ["Call 112 now. Tele MANAS on 14416."], mode: "emergency" })} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Get medical help now");
    expect(within(alert).getByRole("link", { name: /Call 112/ })).toHaveAttribute("href", "tel:112");
    expect(within(alert).getByRole("link", { name: /14416/ })).toHaveAttribute("href", "tel:14416");
  });
});

describe("AssistantPage", () => {
  beforeEach(() => {
    ask.mockReset().mockResolvedValue({ conversation_id: "c1", saved: true, message: { id: "m1", role: "assistant", created_at: null, answer: answer() } });
  });

  it("asks and shows the answer; saved chats continue the conversation", async () => {
    wrap(<AssistantPage />);
    await userEvent.click(screen.getByRole("button", { name: "When is my next appointment?" }));
    expect(ask).toHaveBeenCalledWith({ question: "When is my next appointment?", save: true, conversation_id: null, history: [] });
    expect(await screen.findByText("You take Placeholder A 500 mg at 08:00.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your question"), "And tomorrow?{Enter}");
    expect(ask).toHaveBeenLastCalledWith({ question: "And tomorrow?", save: true, conversation_id: "c1", history: [] });
  });

  it("private chats store nothing and send earlier turns themselves", async () => {
    ask.mockResolvedValue({ conversation_id: null, saved: false, message: { id: null, role: "assistant", created_at: null, answer: answer() } });
    wrap(<AssistantPage />);
    await userEvent.click(screen.getByRole("checkbox", { name: /Save this chat/ }));
    expect(screen.getByText("Private chat: nothing is saved")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Your question"), "First question{Enter}");
    await screen.findByText("You take Placeholder A 500 mg at 08:00.");
    await userEvent.type(screen.getByLabelText("Your question"), "Second question{Enter}");
    const last = ask.mock.calls.at(-1)![0];
    expect(last.save).toBe(false);
    expect(last.conversation_id).toBeNull();
    expect(last.history.map((t: { role: string }) => t.role)).toEqual(["user", "assistant"]);
    expect(last.history[0].content).toBe("First question");
  });

  it("shows errors without losing the question", async () => {
    ask.mockRejectedValue(new Error("The assistant is busy."));
    wrap(<AssistantPage />);
    await userEvent.type(screen.getByLabelText("Your question"), "Anything{Enter}");
    expect(await screen.findByText("Anything")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
