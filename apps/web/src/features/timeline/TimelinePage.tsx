import { Thermometer } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/layout/PortalShell";
import { Button } from "@/components/ui";
import { useActivePatient } from "@/features/patient/context";

import { patientLink } from "./links";
import { AddSymptomDialog } from "./SymptomDialogs";
import { TimelineView } from "./TimelineView";

/** The patient's whole record in date order (also a caregiver's view of the person). */
export function RecordTimelinePage() {
  const { patientId, base, mode, can, name } = useActivePatient();
  const self = mode === "self";
  const [reporting, setReporting] = useState(false);
  const canReport = can("report_health_info");

  return (
    <>
      <PageHeader
        title={self ? "My health timeline" : `${name ?? "Their"} health timeline`}
        description={
          self
            ? "Everything in your record in date order: visits, symptoms, tests, prescriptions and documents. Filter by date, doctor, specialty or type."
            : "Everything you are allowed to see for this person, in date order."
        }
        actions={
          canReport && (
            <Button icon={<Thermometer className="size-4" />} onClick={() => setReporting(true)}>
              Report a symptom
            </Button>
          )
        }
      />
      <TimelineView
        patientId={patientId}
        variant="patient"
        self={self}
        linkFor={(e) => patientLink(e, base)}
        correctable={canReport ? ["patient", "caregiver"] : []}
      />
      {reporting && <AddSymptomDialog patientId={patientId} as="report" onClose={() => setReporting(false)} />}
    </>
  );
}
