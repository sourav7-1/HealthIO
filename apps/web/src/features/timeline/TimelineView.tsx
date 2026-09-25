/**
 * The medical record timeline, in two voices:
 *
 * - **patient**: grouped by month, plain-language headlines, who added each record.
 * - **doctor**: grouped by day, compact rows with record type, status, author and
 *   specialty, and links into the chart.
 *
 * The server decides what is visible (permissions and consent) and applies the filters.
 * Corrected records carry a "Corrected" mark and open their full history.
 */
import { ArrowDownUp, History, ListFilter, PencilLine, X } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { Badge, Button, Card, EmptyState, Field, Input, Select, cn } from "@/components/ui";
import { QueryState, StatusBadge } from "@/features/chart/shared";
import { formatDate, formatTime } from "@/lib/format";

import {
  NO_FILTERS,
  filterCount,
  useSymptoms,
  useTimeline,
  type TimelineEvent,
  type TimelineFilters,
  type TimelineKind,
} from "./api";
import { HistoryDialog } from "./HistoryDialog";
import { KINDS, KIND_ORDER, SOURCE_LABEL, friendlyTitle } from "./kinds";
import { CorrectSymptomDialog } from "./SymptomDialogs";

export type Variant = "patient" | "doctor";

interface Props {
  patientId: string;
  variant: Variant;
  /** Patient view: true when the signed-in person is the patient ("you"). */
  self?: boolean;
  /** Where a record opens in this portal (null: no page for it). */
  linkFor?: (e: TimelineEvent) => string | null;
  /** Record sources this viewer may correct (e.g. patient and caregiver entries). */
  correctable?: string[];
  actions?: ReactNode;
}

// Statuses worth showing in the patient view (others are routine and add noise).
const PATIENT_STATUSES = new Set([
  "cancelled",
  "entered_in_error",
  "superseded",
  "pending_review",
  "rejected",
  "resolved",
  "pending_confirmation",
  "missed",
  "no_show",
  "open",
  "pending_scan",
]);

function localDate(e: TimelineEvent): Date {
  return e.date_only ? new Date(`${e.at.slice(0, 10)}T12:00:00`) : new Date(e.at);
}

/** The record's day in the viewer's time zone (dates without a time stay as written). */
function dayOf(e: TimelineEvent): string {
  if (e.date_only) return e.at.slice(0, 10);
  const d = localDate(e);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function groupKey(e: TimelineEvent, variant: Variant): string {
  return variant === "patient" ? dayOf(e).slice(0, 7) : dayOf(e);
}

function groupLabel(key: string, variant: Variant): string {
  if (variant === "doctor") return formatDate(key);
  const [y = 1970, m = 1] = key.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function sourceText(e: TimelineEvent, self: boolean): string | null {
  if (!e.source) return null;
  if (e.source === "patient") return self ? "Added by you" : "Added by the patient";
  if (e.source === "caregiver") return "Added by a caregiver";
  if (e.source === "doctor" || e.source === "doctor_issued" || e.source === "clinician_recorded") {
    return e.doctor?.name ? `Recorded by ${e.doctor.name}` : "Recorded by a doctor";
  }
  return SOURCE_LABEL[e.source] ?? null;
}

export function TimelineView({ patientId, variant, self = false, linkFor, correctable = [], actions }: Props) {
  const [filters, setFilters] = useState<TimelineFilters>(NO_FILTERS);
  const [showFilters, setShowFilters] = useState(false);
  const [historyOf, setHistoryOf] = useState<TimelineEvent | null>(null);
  const [correcting, setCorrecting] = useState<string | null>(null);
  const timeline = useTimeline(patientId, filters);
  const symptoms = useSymptoms(patientId, correctable.length > 0 && correcting !== null);
  const pages = timeline.data?.pages;
  const facets = pages?.[0]?.facets;
  const events = useMemo(() => (pages ?? []).flatMap((p) => p.items), [pages]);
  const total = pages?.[0]?.total ?? 0;
  const active = filterCount(filters);

  const groups = useMemo(() => {
    const out: { key: string; items: TimelineEvent[] }[] = [];
    for (const e of events) {
      const key = groupKey(e, variant);
      if (out.at(-1)?.key !== key) out.push({ key, items: [] });
      out.at(-1)!.items.push(e);
    }
    return out;
  }, [events, variant]);

  const toggleKind = (k: TimelineKind) =>
    setFilters((f) => ({ ...f, kinds: f.kinds.includes(k) ? f.kinds.filter((x) => x !== k) : [...f.kinds, k] }));

  const available = KIND_ORDER.filter((k) => facets?.kinds.some((f) => f.value === k) || filters.kinds.includes(k));
  const correctingSymptom = symptoms.data?.find((s) => s.id === correcting);
  const canCorrect = (e: TimelineEvent) =>
    e.kind === "symptom" && e.status !== "entered_in_error" && !!e.source && correctable.includes(e.source);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={showFilters || active ? "primary" : "secondary"}
          size="sm"
          icon={<ListFilter className="size-4" />}
          aria-expanded={showFilters}
          aria-controls="timeline-filters"
          onClick={() => setShowFilters((s) => !s)}
        >
          Filters{active ? ` (${active})` : ""}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          icon={<ArrowDownUp className="size-4" />}
          onClick={() => setFilters((f) => ({ ...f, order: f.order === "newest" ? "oldest" : "newest" }))}
        >
          {filters.order === "newest" ? "Newest first" : "Oldest first"}
        </Button>
        {active > 0 && (
          <Button variant="ghost" size="sm" icon={<X className="size-4" />} onClick={() => setFilters({ ...NO_FILTERS, order: filters.order })}>
            Clear filters
          </Button>
        )}
        <p className="text-sm text-muted" aria-live="polite">
          {timeline.isSuccess && `${total} record${total === 1 ? "" : "s"}`}
        </p>
        {actions && <div className="ml-auto flex flex-wrap gap-2">{actions}</div>}
      </div>

      {available.length > 1 && (
        <div className="flex flex-wrap gap-2" role="group" aria-label="Record types">
          {available.map((k) => {
            const meta = KINDS[k];
            const count = facets?.kinds.find((f) => f.value === k)?.count;
            const on = filters.kinds.includes(k);
            return (
              <button
                key={k}
                type="button"
                aria-pressed={on}
                onClick={() => toggleKind(k)}
                className={cn(
                  "inline-flex min-h-9 items-center gap-1.5 rounded-full border px-3 text-sm",
                  on ? "border-accent bg-accent text-accent-fg" : "border-line text-muted hover:text-fg",
                )}
              >
                <meta.icon className="size-4" aria-hidden />
                {variant === "patient" ? meta.friendly : meta.label}
                {count !== undefined && <span className="tabular-nums opacity-75">{count}</span>}
              </button>
            );
          })}
        </div>
      )}

      {showFilters && (
        <Card>
          <div id="timeline-filters" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="From">
              {(p) => <Input {...p} type="date" value={filters.dateFrom} max={filters.dateTo || undefined} onChange={(e) => setFilters((f) => ({ ...f, dateFrom: e.target.value }))} />}
            </Field>
            <Field label="To">
              {(p) => <Input {...p} type="date" value={filters.dateTo} min={filters.dateFrom || undefined} onChange={(e) => setFilters((f) => ({ ...f, dateTo: e.target.value }))} />}
            </Field>
            <Field label="Doctor">
              {(p) => (
                <Select {...p} value={filters.doctorId} onChange={(e) => setFilters((f) => ({ ...f, doctorId: e.target.value }))}>
                  <option value="">All doctors</option>
                  {(facets?.doctors ?? []).map((d) => (
                    <option key={d.id} value={d.id ?? ""}>
                      {d.name}
                      {d.specialty ? ` (${d.specialty})` : ""}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Field label="Specialty">
              {(p) => (
                <Select {...p} value={filters.specialty} onChange={(e) => setFilters((f) => ({ ...f, specialty: e.target.value }))}>
                  <option value="">All specialties</option>
                  {(facets?.specialties ?? []).map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.value}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
          </div>
        </Card>
      )}

      <QueryState
        query={timeline}
        what="Timeline"
        rows={5}
        isEmpty={(d) => d.pages[0]?.items.length === 0}
        empty={
          <Card>
            <EmptyState
              icon={<History className="size-5" />}
              title={active ? "Nothing matches these filters" : "Nothing recorded yet"}
              description={
                active
                  ? "Try a wider date range or fewer record types."
                  : variant === "patient"
                    ? "Visits, tests, prescriptions, symptoms and documents appear here in date order as they are added."
                    : "Everything recorded for this patient that you are allowed to see appears here in date order."
              }
            />
          </Card>
        }
      >
        {() => (
          <div className="flex flex-col gap-6">
            {groups.map((g) => (
              <section key={g.key} aria-labelledby={`tl-${g.key}`}>
                <h2
                  id={`tl-${g.key}`}
                  className={cn(
                    "sticky top-0 z-[1] mb-3 bg-bg/95 py-1 backdrop-blur",
                    variant === "patient" ? "text-base font-semibold" : "text-xs font-semibold uppercase tracking-wide text-muted",
                  )}
                >
                  {groupLabel(g.key, variant)}
                </h2>
                {variant === "patient" ? (
                  <ol className="relative flex flex-col gap-3 border-l-2 border-line pl-6">
                    {g.items.map((e) => (
                      <PatientItem
                        key={e.key}
                        e={e}
                        self={self}
                        to={linkFor?.(e) ?? null}
                        onHistory={() => setHistoryOf(e)}
                        onCorrect={canCorrect(e) ? () => setCorrecting(e.resource_id) : undefined}
                      />
                    ))}
                  </ol>
                ) : (
                  <Card bodyClassName="p-0">
                    <ol className="divide-y divide-line">
                      {g.items.map((e) => (
                        <DoctorRow
                          key={e.key}
                          e={e}
                          to={linkFor?.(e) ?? null}
                          onHistory={() => setHistoryOf(e)}
                          onCorrect={canCorrect(e) ? () => setCorrecting(e.resource_id) : undefined}
                        />
                      ))}
                    </ol>
                  </Card>
                )}
              </section>
            ))}
            {timeline.hasNextPage && (
              <div className="flex justify-center">
                <Button variant="secondary" loading={timeline.isFetchingNextPage} onClick={() => void timeline.fetchNextPage()}>
                  Show older records
                </Button>
              </div>
            )}
          </div>
        )}
      </QueryState>

      {historyOf && <HistoryDialog patientId={patientId} event={historyOf} self={self} onClose={() => setHistoryOf(null)} />}
      {correctingSymptom && (
        <CorrectSymptomDialog patientId={patientId} symptom={correctingSymptom} onClose={() => setCorrecting(null)} />
      )}
    </div>
  );
}

function Headline({ to, children }: { to: string | null; children: ReactNode }) {
  return to ? (
    <Link to={to} className="hover:underline">
      {children}
    </Link>
  ) : (
    <>{children}</>
  );
}

function RecordActions({ e, onHistory, onCorrect }: { e: TimelineEvent; onHistory: () => void; onCorrect?: () => void }) {
  return (
    <>
      {onCorrect && (
        <Button size="sm" variant="ghost" icon={<PencilLine className="size-4" />} onClick={onCorrect}>
          Correct
        </Button>
      )}
      {e.has_history && (
        <Button size="sm" variant="ghost" icon={<History className="size-4" />} onClick={onHistory} aria-label={`History of: ${e.title}`}>
          History
        </Button>
      )}
    </>
  );
}

function PatientItem({
  e,
  self,
  to,
  onHistory,
  onCorrect,
}: {
  e: TimelineEvent;
  self: boolean;
  to: string | null;
  onHistory: () => void;
  onCorrect?: () => void;
}) {
  const meta = KINDS[e.kind];
  const source = sourceText(e, self);
  return (
    <li className="relative">
      <span className={cn("absolute -left-[2.35rem] top-2 grid size-8 place-items-center rounded-full ring-4 ring-bg", meta.tone)} aria-hidden>
        <meta.icon className="size-4" />
      </span>
      <div className="rounded-xl border border-line bg-surface px-4 py-3 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-wide text-muted">{meta.friendly}</p>
            <p className="font-semibold">
              <Headline to={to}>{friendlyTitle(e, self)}</Headline>
            </p>
          </div>
          <time dateTime={e.at} className="shrink-0 text-sm text-muted tabular-nums">
            {formatDate(dayOf(e))}
          </time>
        </div>
        {e.detail && <p className="mt-1 text-sm">{e.detail}</p>}
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          {e.doctor?.specialty && <Badge tone="accent">{e.doctor.specialty}</Badge>}
          {source && <span className="text-muted">{source}</span>}
          {e.status && PATIENT_STATUSES.has(e.status) && <StatusBadge status={e.status} />}
          {e.amended && <Badge tone="warning">Corrected</Badge>}
          <span className="ml-auto flex gap-1">
            <RecordActions e={e} onHistory={onHistory} onCorrect={onCorrect} />
          </span>
        </div>
      </div>
    </li>
  );
}

function DoctorRow({
  e,
  to,
  onHistory,
  onCorrect,
}: {
  e: TimelineEvent;
  to: string | null;
  onHistory: () => void;
  onCorrect?: () => void;
}) {
  const meta = KINDS[e.kind];
  return (
    <li className="grid grid-cols-[4.5rem_1fr] gap-3 px-4 py-3 sm:grid-cols-[4.5rem_9rem_1fr_auto]">
      <time dateTime={e.at} className="pt-0.5 text-sm text-muted tabular-nums">
        {e.date_only ? "—" : formatTime(e.at)}
      </time>
      <span className={cn("hidden h-fit w-fit items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium sm:inline-flex", meta.tone)}>
        <meta.icon className="size-3.5" aria-hidden />
        {meta.label}
      </span>
      <div className="col-start-2 min-w-0 sm:col-start-3">
        <p className="font-medium">
          <span className="sr-only">{meta.label}: </span>
          <Headline to={to}>{e.title}</Headline>
        </p>
        {e.detail && <p className="line-clamp-2 text-sm text-muted">{e.detail}</p>}
        <p className="mt-0.5 text-xs text-muted">
          {[e.doctor?.name, e.doctor?.specialty, e.source && e.source !== "doctor" ? `source: ${SOURCE_LABEL[e.source] ?? e.source}` : null]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      <div className="col-start-2 flex flex-wrap items-center gap-1.5 sm:col-start-4 sm:justify-end">
        {e.amended && <Badge tone="warning">Amended</Badge>}
        <StatusBadge status={e.status} />
        <RecordActions e={e} onHistory={onHistory} onCorrect={onCorrect} />
      </div>
    </li>
  );
}
