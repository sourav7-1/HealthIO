/**
 * What Health Io shows for a missed dose: the instructions as written (and whether they
 * come from a verified prescription) plus fixed text that points to a doctor or
 * pharmacist. It never says whether to take the dose now; that text comes from the API.
 */
import { Info } from "lucide-react";

import type { Schemas } from "@/lib/api";

export type Guidance = Schemas["GuidanceOut"];

export function MissedGuidance({ guidance }: { guidance: Guidance }) {
  return (
    <div className="mt-3 rounded-lg bg-surface-2 px-3 py-2.5 text-sm" role="note" aria-label="About this missed dose">
      <p className="flex items-center gap-1.5 font-semibold">
        <Info className="size-4" aria-hidden /> About this missed dose
      </p>
      {guidance.instructions_as_written ? (
        <p className="mt-1.5">
          <span className="font-medium">
            {guidance.instructions_verified ? "What the prescription says: " : "Your own note (not from a prescription): "}
          </span>
          {guidance.instructions_as_written}
        </p>
      ) : (
        <p className="mt-1.5 text-muted">No instructions were recorded for this medicine.</p>
      )}
      <p className="mt-1.5">{guidance.message}</p>
    </div>
  );
}
