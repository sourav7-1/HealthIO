/**
 * One assistant answer. Every part says where it comes from:
 * - "From your record" (with the record items it relies on),
 * - "General information" (with the reviewed source, publisher and review date),
 * - "Not sure" (nothing to support it: ask a doctor or pharmacist).
 * Emergency guidance, when present, comes first and cannot be missed.
 */
import { BookOpen, CircleHelp, ExternalLink, FileText, Phone, Siren } from "lucide-react";

import { cn } from "@/components/ui";
import { formatDate } from "@/lib/format";

import type { Answer, AnswerSegment } from "./api";

const KIND: Record<AnswerSegment["kind"], { label: string; icon: typeof FileText; tone: string }> = {
  record: { label: "From the record", icon: FileText, tone: "border-accent/40 bg-accent/5" },
  general: { label: "General information", icon: BookOpen, tone: "border-info/40 bg-info/5" },
  uncertain: { label: "Not sure", icon: CircleHelp, tone: "border-warning/50 bg-warning/5" },
};

function Emergency({ texts }: { texts: string[] }) {
  return (
    <div role="alert" className="rounded-xl border-2 border-danger bg-danger/10 p-4">
      <p className="flex items-center gap-2 font-semibold text-danger">
        <Siren className="size-5" aria-hidden /> Get medical help now
      </p>
      {texts.map((t) => (
        <p key={t} className="mt-2 text-sm">
          {t}
        </p>
      ))}
      <div className="mt-3 flex flex-wrap gap-2">
        <a href="tel:112" className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-danger px-4 text-sm font-semibold text-white">
          <Phone className="size-4" aria-hidden /> Call 112
        </a>
        <a href="tel:108" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-danger px-4 text-sm font-semibold text-danger">
          Ambulance 108
        </a>
        {texts.some((t) => t.includes("14416")) && (
          <a href="tel:14416" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-danger px-4 text-sm font-semibold text-danger">
            Tele MANAS 14416
          </a>
        )}
      </div>
    </div>
  );
}

function Segment({ s }: { s: AnswerSegment }) {
  const meta = KIND[s.kind];
  return (
    <section className={cn("rounded-lg border-l-4 px-3 py-2", meta.tone)} aria-label={meta.label}>
      <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
        <meta.icon className="size-3.5" aria-hidden /> {meta.label}
      </p>
      <p className="mt-1 whitespace-pre-line text-sm">{s.text}</p>
      {s.sources.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Sources">
          {s.sources.map((src) =>
            src.kind === "library" && src.url ? (
              <li key={src.id}>
                <a
                  href={src.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 rounded-full bg-surface px-2 py-0.5 text-xs text-info ring-1 ring-line hover:underline"
                >
                  {src.publisher}: {src.label}
                  {src.reviewed_on && <span className="text-muted">· reviewed {formatDate(src.reviewed_on)}</span>}
                  <ExternalLink className="size-3" aria-hidden />
                </a>
              </li>
            ) : (
              <li key={src.id} className="rounded-full bg-surface px-2 py-0.5 text-xs text-muted ring-1 ring-line">
                {src.label}
              </li>
            ),
          )}
        </ul>
      )}
    </section>
  );
}

export function AnswerCard({ answer }: { answer: Answer }) {
  return (
    <div className="flex flex-col gap-2">
      {answer.emergency.length > 0 && <Emergency texts={answer.emergency} />}
      {answer.segments.map((s, i) => (
        <Segment key={i} s={s} />
      ))}
      {answer.questions_for_doctor.length > 0 && (
        <div className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
          <p className="font-medium">You could ask your doctor or pharmacist:</p>
          <ul className="mt-1 list-disc pl-5">
            {answer.questions_for_doctor.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </div>
      )}
      {answer.mode === "offline" && <p className="text-xs text-muted">AI answers are switched off on this server.</p>}
    </div>
  );
}
