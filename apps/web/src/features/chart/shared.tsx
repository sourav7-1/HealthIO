import type { UseQueryResult } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import type { ReactNode } from "react";

import { Badge, EmptyState, ErrorState, SkeletonList } from "@/components/ui";
import { ApiError, errorMessage } from "@/lib/api";
import { humanize } from "@/lib/format";

type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

const STATUS_TONES: Record<string, Tone> = {
  // visits, notes, prescriptions
  in_progress: "info",
  planned: "neutral",
  completed: "success",
  draft: "warning",
  signed: "success",
  superseded: "neutral",
  issued: "success",
  recorded: "success",
  cancelled: "neutral",
  entered_in_error: "danger",
  // medications
  pending_confirmation: "warning",
  active: "success",
  paused: "neutral",
  stopped: "neutral",
  // tests
  ordered: "info",
  sample_collected: "info",
  partially_resulted: "info",
  pending_review: "warning",
  verified: "success",
  rejected: "danger",
  // appointments and follow-ups
  requested: "warning",
  scheduled: "info",
  confirmed: "info",
  checked_in: "accent",
  no_show: "danger",
  open: "info",
  booked: "accent",
  // diagnoses (as documented)
  provisional: "warning",
  unconfirmed: "warning",
  refuted: "neutral",
};

export function StatusBadge({ status, label }: { status: string | null | undefined; label?: string }) {
  if (!status) return null;
  return <Badge tone={STATUS_TONES[status] ?? "neutral"}>{label ?? humanize(status)}</Badge>;
}

export function PatientName({ name }: { name: string | null | undefined }) {
  return name ? <>{name}</> : <span className="italic text-muted">Name not shared</span>;
}

export function NotShared({ what }: { what: string }) {
  return (
    <EmptyState
      icon={<Lock className="size-5" />}
      title={`${what} not shared with you`}
      description="This patient has not consented to share this information with you. They can change what they share at any time."
    />
  );
}

/**
 * Standard loading / error / empty / data rendering for a query.
 * A 403 means the patient's consent does not cover this section.
 */
export function QueryState<T>({
  query,
  what,
  isEmpty,
  empty,
  children,
  rows = 3,
}: {
  query: UseQueryResult<T>;
  what: string;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
  rows?: number;
}) {
  if (query.isPending) return <SkeletonList rows={rows} />;
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 403) return <NotShared what={what} />;
    return <ErrorState message={errorMessage(query.error)} onRetry={() => void query.refetch()} />;
  }
  if (isEmpty?.(query.data) && empty) return <>{empty}</>;
  return <>{children(query.data)}</>;
}

export function DefinitionList({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
      {items.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-muted">{k}</dt>
          <dd className="mt-0.5 break-words font-medium">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Provenance label shown next to clinical data (AI_SAFETY.md §11). */
export function SourceLabel({ source }: { source: string }) {
  const labels: Record<string, string> = {
    doctor: "Doctor",
    patient: "Patient-reported",
    caregiver: "Caregiver-reported",
    clinician_recorded: "Recorded by clinician",
    self_reported: "Patient-reported",
    prescription: "From prescription",
    ai_extraction: "Extracted, verified by a person",
    integration: "Imported",
    system: "System",
  };
  return <span className="text-xs text-muted">{labels[source] ?? humanize(source)}</span>;
}
