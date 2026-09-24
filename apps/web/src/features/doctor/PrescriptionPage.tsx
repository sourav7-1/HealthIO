import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { PrescriptionView } from "@/features/chart/PrescriptionView";

export function PrescriptionPage() {
  const { patientId = "", prescriptionId = "" } = useParams();
  return (
    <>
      <PageHeader
        back={
          <Link to={`/doctor/patients/${patientId}/prescriptions`} className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
            <ArrowLeft className="size-4" aria-hidden /> Prescriptions
          </Link>
        }
        title="Prescription"
      />
      <PrescriptionView
        patientId={patientId}
        prescriptionId={prescriptionId}
        hrefFor={(id) => `/doctor/patients/${patientId}/prescriptions/${id}`}
      />
    </>
  );
}
