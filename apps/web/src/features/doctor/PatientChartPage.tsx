import {
  ArrowLeft,
  CalendarPlus,
  ClipboardList,
  ClipboardPlus,
  FilePlus2,
  FlaskConical,
  Stethoscope,
  UserX,
} from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Badge, Button, EmptyState, ErrorState, Skeleton, TabLinks } from "@/components/ui";
import { ApiError, errorMessage } from "@/lib/api";
import { ageFrom, humanize } from "@/lib/format";

import { useFollowUps, useOverview, useTestOrders, type Overview } from "@/features/chart/api";
import { UploadReportDialog, OrderTestsDialog } from "./forms/tests";
import { PrescriptionDialog } from "./forms/prescription";
import { AppointmentDialog, FollowUpDialog } from "./forms/scheduling";
import { RecordVisitDialog } from "./forms/visit";
import {
  AppointmentsSection,
  FollowUpsSection,
  OverviewSection,
  TimelineSection,
  VisitsSection,
} from "./sections/clinical";
import { MedicationsSection, PrescriptionsSection, ReportsSection, TestsSection } from "./sections/orders";

type ChartAction = "visit" | "prescription" | "tests" | "report" | "follow-up" | "appointment";

const TABS = [
  { key: "overview", label: "Overview", needs: null },
  { key: "timeline", label: "Timeline", needs: null },
  { key: "visits", label: "Visits", needs: "view_visits" },
  { key: "medications", label: "Medications", needs: "view_medications" },
  { key: "prescriptions", label: "Prescriptions", needs: "view_prescriptions" },
  { key: "tests", label: "Tests", needs: "view_reports" },
  { key: "reports", label: "Reports", needs: "view_reports" },
  { key: "appointments", label: "Appointments", needs: "view_appointments" },
  { key: "follow-ups", label: "Follow-ups", needs: "view_appointments" },
] as const;

function can(overview: Overview, permission: string): boolean {
  return overview.permissions.includes(permission);
}

export function PatientChartPage() {
  const { patientId = "", tab = "overview" } = useParams();
  const overview = useOverview(patientId);
  const [action, setAction] = useState<ChartAction | null>(null);

  if (overview.isPending) {
    return (
      <div role="status" aria-label="Loading patient">
        <Skeleton className="mb-2 h-4 w-24" />
        <Skeleton className="mb-6 h-8 w-72" />
        <Skeleton className="mb-6 h-11 w-full" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }
  if (overview.isError) {
    if (overview.error instanceof ApiError && overview.error.status === 404) {
      return (
        <EmptyState
          icon={<UserX className="size-5" />}
          title="Patient not available"
          description="This patient does not exist or is not connected to you. You can only open charts of your own patients."
          action={
            <Link to="/doctor/patients" className="text-sm font-medium text-accent hover:underline">
              Back to patients
            </Link>
          }
        />
      );
    }
    return <ErrorState message={errorMessage(overview.error)} onRetry={() => void overview.refetch()} />;
  }

  const ov = overview.data;
  const profile = ov.profile;
  const age = ageFrom(profile?.date_of_birth);
  const tabs = TABS.map((t) => ({
    key: t.key,
    label: t.label,
    to: `/doctor/patients/${patientId}/${t.key}`,
    hidden: t.needs !== null && !can(ov, t.needs),
  }));
  const active = tabs.some((t) => t.key === tab && !t.hidden) ? tab : "overview";

  const actions: { key: ChartAction; label: string; icon: React.ReactNode; needs: string }[] = [
    { key: "visit", label: "Record visit", icon: <Stethoscope className="size-4" />, needs: "edit_clinical_records" },
    { key: "prescription", label: "Prescribe", icon: <ClipboardPlus className="size-4" />, needs: "change_doctor_prescription" },
    { key: "tests", label: "Order tests", icon: <FlaskConical className="size-4" />, needs: "edit_clinical_records" },
    { key: "report", label: "Upload report", icon: <FilePlus2 className="size-4" />, needs: "upload_reports" },
    { key: "follow-up", label: "Set follow-up", icon: <ClipboardList className="size-4" />, needs: "edit_clinical_records" },
    { key: "appointment", label: "Book appointment", icon: <CalendarPlus className="size-4" />, needs: "manage_appointments" },
  ];
  const allowed = actions.filter((a) => can(ov, a.needs));

  return (
    <>
      <PageHeader
        back={
          <Link to="/doctor/patients" className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
            <ArrowLeft className="size-4" aria-hidden /> Patients
          </Link>
        }
        title={profile ? profile.display_name : <span className="italic text-muted">Name not shared</span>}
        description={
          <span className="flex flex-wrap items-center gap-2">
            {profile ? (
              <>
                <span>{age !== null ? `${age} years` : "Age not recorded"}</span>
                {profile.sex_at_birth !== "unknown" && <span>· {humanize(profile.sex_at_birth)}</span>}
                {profile.blood_group !== "unknown" && <span>· Blood group {profile.blood_group}</span>}
                <Badge tone={profile.has_account ? "info" : "neutral"}>
                  {profile.has_account ? "Uses Health Io" : "Registered by clinic"}
                </Badge>
              </>
            ) : (
              <span>The patient has not shared their personal details with you.</span>
            )}
          </span>
        }
      />

      {allowed.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {allowed.map((a) => (
            <Button key={a.key} variant={a.key === "visit" ? "primary" : "secondary"} size="sm" icon={a.icon} onClick={() => setAction(a.key)}>
              {a.label}
            </Button>
          ))}
        </div>
      )}

      <TabLinks tabs={tabs} active={active} label="Patient record sections" />

      <div className="mt-6">
        {active === "overview" && <OverviewSection patientId={patientId} overview={ov} />}
        {active === "timeline" && <TimelineSection patientId={patientId} overview={ov} />}
        {active === "visits" && <VisitsSection patientId={patientId} canRecord={can(ov, "edit_clinical_records")} onRecord={() => setAction("visit")} />}
        {active === "medications" && <MedicationsSection patientId={patientId} overview={ov} />}
        {active === "prescriptions" && <PrescriptionsSection patientId={patientId} canWrite={can(ov, "change_doctor_prescription")} onNew={() => setAction("prescription")} />}
        {active === "tests" && <TestsSection patientId={patientId} canOrder={can(ov, "edit_clinical_records")} onOrder={() => setAction("tests")} />}
        {active === "reports" && <ReportsSection patientId={patientId} canUpload={can(ov, "upload_reports")} onUpload={() => setAction("report")} />}
        {active === "appointments" && <AppointmentsSection patientId={patientId} canBook={can(ov, "manage_appointments")} onBook={() => setAction("appointment")} />}
        {active === "follow-ups" && <FollowUpsSection patientId={patientId} canManage={can(ov, "edit_clinical_records")} onNew={() => setAction("follow-up")} />}
      </div>

      <ChartDialogs patientId={patientId} overview={ov} action={action} onClose={() => setAction(null)} />
    </>
  );
}

function ChartDialogs({
  patientId,
  overview,
  action,
  onClose,
}: {
  patientId: string;
  overview: Overview;
  action: ChartAction | null;
  onClose: () => void;
}) {
  const orders = useTestOrders(patientId, action === "report" && can(overview, "view_reports"));
  const followUps = useFollowUps(patientId, action === "appointment" && can(overview, "view_appointments"));
  const openOrders = (orders.data ?? []).filter((o) => ["ordered", "sample_collected", "partially_resulted"].includes(o.status));
  const openFollowUps = (followUps.data ?? []).filter((f) => f.status === "open");
  return (
    <>
      <RecordVisitDialog patientId={patientId} open={action === "visit"} onClose={onClose} />
      <PrescriptionDialog patientId={patientId} open={action === "prescription"} onClose={onClose} />
      <OrderTestsDialog patientId={patientId} open={action === "tests"} onClose={onClose} />
      <UploadReportDialog patientId={patientId} openOrders={openOrders} open={action === "report"} onClose={onClose} />
      <FollowUpDialog patientId={patientId} open={action === "follow-up"} onClose={onClose} />
      <AppointmentDialog patientId={patientId} openFollowUps={openFollowUps} open={action === "appointment"} onClose={onClose} />
    </>
  );
}
