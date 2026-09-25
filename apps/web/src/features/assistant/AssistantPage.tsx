/**
 * Health assistant chat. Answers are labelled by where they come from, emergencies get
 * urgent guidance first, and the person controls what is kept:
 * - "Save this chat" off = private: nothing is stored; the earlier turns stay in this tab.
 * - Settings: how long saved chats are kept, private by default, whether the assistant
 *   may read the record at all, and deleting all chats.
 */
import { EyeOff, History, MessageSquarePlus, Send, Settings2, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Button, Card, Checkbox, Dialog, Field, Select, Textarea, cn, useToast } from "@/components/ui";
import { useActivePatient } from "@/features/patient/context";
import { errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

import { AnswerCard } from "./AnswerCard";
import {
  STARTERS,
  useAsk,
  useAssistantPreferences,
  useConversation,
  useConversations,
  useDeleteConversation,
  useUpdateAssistantPreferences,
  type Answer,
  type Turn,
} from "./api";

interface Exchange {
  question: string;
  answer: Answer | null; // null while waiting
  error?: string;
}

function answerText(a: Answer): string {
  return a.segments.map((s) => s.text).join(" ");
}

export function AssistantPage() {
  const { patientId, mode, name } = useActivePatient();
  const self = mode === "self";
  const prefs = useAssistantPreferences();
  const conversations = useConversations(patientId);
  const ask = useAsk(patientId);
  const remove = useDeleteConversation(patientId);
  const toast = useToast();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const loaded = useConversation(patientId, conversationId);
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [save, setSave] = useState<boolean | null>(null);
  const [draft, setDraft] = useState("");
  const [settings, setSettings] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const saving = save ?? prefs.data?.save_by_default ?? true;

  // Opening a saved chat shows its history.
  useEffect(() => {
    if (!loaded.data) return;
    const out: Exchange[] = [];
    for (const m of loaded.data.messages) {
      if (m.role === "user") out.push({ question: m.question ?? "", answer: null });
      else if (out.length && m.answer) out[out.length - 1]!.answer = m.answer;
    }
    setExchanges(out);
  }, [loaded.data]);

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [exchanges.length]);

  const newChat = () => {
    setConversationId(null);
    setExchanges([]);
    setDraft("");
  };

  const send = async (question: string) => {
    const q = question.trim();
    if (q.length < 2 || ask.isPending) return;
    setDraft("");
    const history: Turn[] = saving
      ? []
      : exchanges
          .filter((e) => e.answer)
          .slice(-6)
          .flatMap((e) => [
            { role: "user" as const, content: e.question.slice(0, 4000) },
            { role: "assistant" as const, content: answerText(e.answer!).slice(0, 4000) },
          ]);
    setExchanges((xs) => [...xs, { question: q, answer: null }]);
    try {
      const res = await ask.mutateAsync({ question: q, save: saving, conversation_id: saving ? conversationId : null, history });
      if (res.saved) setConversationId(res.conversation_id ?? null);
      setExchanges((xs) => xs.map((x, i) => (i === xs.length - 1 ? { ...x, answer: res.message.answer ?? null } : x)));
    } catch (err) {
      setExchanges((xs) => xs.map((x, i) => (i === xs.length - 1 ? { ...x, error: errorMessage(err) } : x)));
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void send(draft);
  };

  const toggleSave = (next: boolean) => {
    // Switching between saved and private starts a new chat, so nothing mixes.
    if (exchanges.length) newChat();
    setSave(next);
  };

  return (
    <>
      <PageHeader
        title="Health assistant"
        description={
          self
            ? "Ask about your medicines, prescriptions, appointments and records, or a general health question."
            : `Ask about ${name ?? "this person"}'s medicines, prescriptions, appointments and records.`
        }
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" icon={<Settings2 className="size-4" />} onClick={() => setSettings(true)}>
              Privacy
            </Button>
            <Button icon={<MessageSquarePlus className="size-4" />} onClick={newChat}>
              New chat
            </Button>
          </div>
        }
      />
      <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
        <Card title="Saved chats" bodyClassName="p-0" className="h-fit">
          {conversations.data?.length ? (
            <ul className="divide-y divide-line">
              {conversations.data.map((c) => (
                <li key={c.id} className={cn("flex items-center gap-1 pr-1", c.id === conversationId && "bg-surface-2")}>
                  <button
                    type="button"
                    className="min-w-0 flex-1 px-4 py-2.5 text-left"
                    aria-current={c.id === conversationId ? "true" : undefined}
                    onClick={() => {
                      setSave(true);
                      setConversationId(c.id);
                    }}
                  >
                    <span className="block truncate text-sm font-medium">{c.title ?? "Chat"}</span>
                    <span className="block text-xs text-muted">{formatDateTime(c.last_message_at)}</span>
                  </button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Delete chat: ${c.title ?? "Chat"}`}
                    onClick={async () => {
                      await remove.mutateAsync(c.id);
                      if (c.id === conversationId) newChat();
                      toast.success("Chat deleted");
                    }}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-4 py-3 text-sm text-muted">
              {prefs.data ? `Saved chats are kept for ${prefs.data.history_days} day${prefs.data.history_days === 1 ? "" : "s"}.` : ""}
            </p>
          )}
        </Card>

        <div className="flex min-w-0 flex-col gap-4">
          <Alert tone="info">
            <span className="flex items-start gap-2">
              <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>
                The assistant explains what is in the record and gives general information from reviewed sources. It can't diagnose,
                prescribe or change treatment. In an emergency, call 112.
              </span>
            </span>
          </Alert>
          {prefs.data && !prefs.data.use_records && (
            <Alert tone="warning">The assistant is not allowed to read the record (Privacy settings). It answers general questions only.</Alert>
          )}

          <div className="flex flex-col gap-5" aria-live="polite">
            {exchanges.length === 0 && (
              <div className="flex flex-col gap-2">
                <p className="text-sm text-muted">Try asking:</p>
                <div className="flex flex-wrap gap-2">
                  {STARTERS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => void send(s)}
                      className="min-h-9 rounded-full border border-line px-3 text-sm hover:bg-surface-2"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {exchanges.map((x, i) => (
              <div key={i} className="flex flex-col gap-2">
                <p className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-4 py-2 text-sm text-accent-fg">{x.question}</p>
                <div className="max-w-[95%]">
                  {x.error ? (
                    <Alert tone="danger">{x.error}</Alert>
                  ) : x.answer ? (
                    <AnswerCard answer={x.answer} />
                  ) : (
                    <p className="text-sm text-muted" role="status">
                      Checking the record and reviewed sources…
                    </p>
                  )}
                </div>
              </div>
            ))}
            <div ref={bottom} />
          </div>

          <form onSubmit={submit} className="sticky bottom-0 flex flex-col gap-2 rounded-xl border border-line bg-surface p-3 shadow-sm">
            <label htmlFor="assistant-question" className="sr-only">
              Your question
            </label>
            <Textarea
              id="assistant-question"
              rows={2}
              maxLength={2000}
              placeholder="Ask a question…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(draft);
                }
              }}
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Checkbox
                checked={saving}
                onChange={(e) => toggleSave(e.target.checked)}
                label={
                  <span className="inline-flex items-center gap-1">
                    {saving ? <History className="size-4" aria-hidden /> : <EyeOff className="size-4" aria-hidden />}
                    {saving ? "Save this chat" : "Private chat: nothing is saved"}
                  </span>
                }
              />
              <Button type="submit" icon={<Send className="size-4" />} loading={ask.isPending} disabled={draft.trim().length < 2}>
                Ask
              </Button>
            </div>
          </form>
        </div>
      </div>
      {settings && <PrivacyDialog patientId={patientId} onClose={() => setSettings(false)} onDeletedAll={newChat} />}
    </>
  );
}

function PrivacyDialog({ patientId, onClose, onDeletedAll }: { patientId: string; onClose: () => void; onDeletedAll: () => void }) {
  const prefs = useAssistantPreferences();
  const update = useUpdateAssistantPreferences();
  const remove = useDeleteConversation(patientId);
  const toast = useToast();
  const p = prefs.data;
  const change = async (body: Parameters<typeof update.mutateAsync>[0]) => {
    try {
      await update.mutateAsync(body);
      toast.success("Saved");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title="Assistant privacy"
      description="These settings are yours. Only you can see your chats, not doctors and not other caregivers."
      footer={<Button variant="secondary" onClick={onClose}>Done</Button>}
    >
      {p && (
        <div className="flex flex-col gap-4">
          <Field label="Keep saved chats for" hint="Shortening this also applies to chats already saved.">
            {(f) => (
              <Select {...f} value={p.history_days} onChange={(e) => void change({ history_days: Number(e.target.value) })}>
                {p.history_day_choices.map((d) => (
                  <option key={d} value={d}>
                    {d} day{d === 1 ? "" : "s"}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Checkbox
            checked={p.save_by_default}
            onChange={(e) => void change({ save_by_default: e.target.checked })}
            label="Save new chats"
            description="Off: new chats are private and nothing is stored."
          />
          <Checkbox
            checked={p.use_records}
            onChange={(e) => void change({ use_records: e.target.checked })}
            label="Let the assistant read the health record"
            description="Off: it answers general questions only and never sees medicines, reports or appointments."
          />
          <div className="border-t border-line pt-4">
            <Button
              variant="danger"
              icon={<Trash2 className="size-4" />}
              loading={remove.isPending}
              onClick={async () => {
                await remove.mutateAsync(null);
                onDeletedAll();
                toast.success("All your chats were deleted");
              }}
            >
              Delete all my chats
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
