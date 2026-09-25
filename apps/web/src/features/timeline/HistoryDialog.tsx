/**
 * Who created and changed a record, when, what changed and why. Nothing in Health Io's
 * clinical record changes silently: every correction is listed here.
 */
import { History } from "lucide-react";

import { Button, Dialog } from "@/components/ui";
import { QueryState } from "@/features/chart/shared";
import { formatDateTime, humanize } from "@/lib/format";

import { useRecordHistory, type HistoryEntry, type TimelineEvent } from "./api";

const ACTION: Record<HistoryEntry["action"], string> = {
  created: "Created",
  changed: "Changed",
  signed: "Signed",
  amended: "Amendment signed",
  revised: "New version issued",
};

function who(e: HistoryEntry, self: boolean): string {
  if (e.actor.role === "doctor") return e.actor.name ?? "A doctor";
  if (e.actor.role === "patient") return self ? "You" : "The patient";
  if (e.actor.role === "caregiver") return "A caregiver";
  return "Health Io";
}

// Status-like values (snake_case codes) read better humanised; free text is shown as written.
const shown = (v: string | null) => (v === null || v === "" ? "—" : /^[a-z]+(_[a-z]+)+$/.test(v) ? humanize(v) : v);

export function HistoryDialog({
  patientId,
  event,
  self,
  onClose,
}: {
  patientId: string;
  event: TimelineEvent;
  self: boolean;
  onClose: () => void;
}) {
  const history = useRecordHistory(patientId, event.kind, event.resource_id);
  return (
    <Dialog
      open
      onClose={onClose}
      title="Record history"
      description={event.title}
      footer={<Button variant="secondary" onClick={onClose}>Close</Button>}
    >
      <QueryState query={history} what="History" isEmpty={(h) => h.entries.length === 0} empty={<p className="text-sm text-muted">No history recorded.</p>}>
        {(h) => (
          <ol className="relative flex flex-col gap-5 border-l border-line pl-5" aria-label="Versions, oldest first">
            {h.entries.map((e, i) => (
              <li key={`${e.at}-${i}`} className="relative">
                <span className="absolute -left-[1.72rem] top-0.5 grid size-5 place-items-center rounded-full border border-line bg-surface text-muted" aria-hidden>
                  <History className="size-3" />
                </span>
                <p className="text-sm">
                  <span className="font-semibold">{ACTION[e.action]}</span>
                  {e.version ? <span className="text-muted"> · version {e.version}</span> : null}
                </p>
                <p className="text-xs text-muted">
                  {who(e, self)} · <time dateTime={e.at}>{formatDateTime(e.at)}</time>
                </p>
                {e.reason && (
                  <p className="mt-1 text-sm">
                    <span className="text-muted">Reason: </span>
                    {e.reason}
                  </p>
                )}
                {e.action === "changed" && e.changes.length > 0 && (
                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 rounded-lg bg-surface-2 px-3 py-2 text-sm">
                    {e.changes.map((c) => (
                      <div key={c.field} className="contents">
                        <dt className="text-muted">{humanize(c.field)}</dt>
                        <dd className="min-w-0 break-words">
                          <del className="text-muted">{shown(c.before)}</del> → <ins className="no-underline">{shown(c.after)}</ins>
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </li>
            ))}
          </ol>
        )}
      </QueryState>
    </Dialog>
  );
}
