import { FileScan, Keyboard, Stethoscope, UserRound } from "lucide-react";

import { Badge } from "@/components/ui";
import { useMode } from "@/features/patient/context";
import { formatDate, formatTime, humanize } from "@/lib/format";

import type { Med } from "./api";

type Origin = Med["origin"];

const BASE = "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold";

/** Where a medicine came from, always in words and an icon (never colour alone). */
export function OriginBadge({ origin }: { origin: Origin }) {
  const self = useMode() === "self";
  const doctor = self ? "your doctor" : "a doctor";
  switch (origin) {
    case "doctor_prescription":
      return <span className={`${BASE} bg-info/12 text-info`}><Stethoscope className="size-3.5" aria-hidden /> Prescribed by {doctor}</span>;
    case "clinician_recorded":
      return <span className={`${BASE} bg-info/12 text-info`}><Stethoscope className="size-3.5" aria-hidden /> Recorded by {doctor}</span>;
    case "uploaded_prescription_ai":
      return <span className={`${BASE} bg-surface-2 text-fg`}><FileScan className="size-3.5" aria-hidden /> Paper prescription · read by AI, checked</span>;
    case "uploaded_prescription_typed":
      return <span className={`${BASE} bg-surface-2 text-fg`}><Keyboard className="size-3.5" aria-hidden /> Paper prescription · typed in</span>;
    case "self_reported":
      return <span className={`${BASE} bg-surface-2 text-muted`}><UserRound className="size-3.5" aria-hidden /> {self ? "Added by you" : "Added by patient or family"}</span>;
    default:
      return <Badge>{humanize(origin)}</Badge>;
  }
}

export const STATUS_LABEL: Record<Med["status"], string> = {
  pending_confirmation: "Waiting to be set up",
  active: "In use",
  paused: "Paused",
  completed: "Course completed",
  stopped: "Discontinued",
  entered_in_error: "Entered in error",
};

export function StatusPill({ status }: { status: Med["status"] }) {
  const tone = status === "active" ? "success" : status === "paused" || status === "pending_confirmation" ? "warning" : "neutral";
  return <Badge tone={tone}>{STATUS_LABEL[status]}</Badge>;
}

export function isPrescribed(origin: Origin): boolean {
  return origin !== "self_reported" && origin !== "integration";
}

const MEAL: Record<string, string> = {
  before_food: "before food",
  after_food: "after food",
  with_food: "with food",
  empty_stomach: "on an empty stomach",
  bedtime: "at bedtime",
};

function t(value: string): string {
  return formatTime(`2000-01-01T${value.slice(0, 5)}:00`);
}

/** "1 tablet · 8:00 am and 8:00 pm · every day · after food". */
export function scheduleText(med: Med): string {
  const s = med.schedule;
  if (med.is_prn || s?.type === "as_needed") return "Only when needed";
  if (!s) return "No schedule";
  const dose = s.dose_amount ? `${Number(s.dose_amount)} ${s.dose_unit ?? ""}`.trim() : null;
  const when =
    s.type === "interval"
      ? `every ${Math.round((s.interval_minutes ?? 0) / 60)} hours from ${t(s.times_of_day[0] ?? "00:00")}`
      : s.times_of_day.map(t).join(", ");
  return [dose, when, s.pattern_text, s.meal_relation && s.meal_relation !== "any" ? MEAL[s.meal_relation] : null].filter(Boolean).join(" · ");
}

export function courseText(med: Med): string | null {
  if (!med.start_date && !med.end_date) return null;
  return [med.start_date ? `from ${formatDate(med.start_date)}` : null, med.end_date ? `until ${formatDate(med.end_date)}` : "no end date"]
    .filter(Boolean)
    .join(" ");
}
