import { useParams } from "react-router";

import { ActivePatientProvider } from "@/features/patient/context";
import { ScanReviewPage } from "@/features/scans/ScanReviewPage";

/** A doctor checks a paper prescription the patient uploaded (saved as doctor-verified). */
export function DoctorScanPage() {
  const { patientId = "" } = useParams();
  return (
    <ActivePatientProvider patientId={patientId} mode="doctor" base={`/doctor/patients/${patientId}`}>
      <ScanReviewPage />
    </ActivePatientProvider>
  );
}
