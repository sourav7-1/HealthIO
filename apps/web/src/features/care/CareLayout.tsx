import {
  Bell,
  CalendarDays,
  FileText,
  FlaskConical,
  GanttChart,
  HeartHandshake,
  HeartPulse,
  History,
  LayoutDashboard,
  Pill,
  Siren,
  Stethoscope,
  UserRound,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";
import { Outlet, useNavigate, useParams } from "react-router";

import { PortalShell, type NavItem } from "@/components/layout/PortalShell";
import { Select } from "@/components/ui";
import { RequireRole } from "@/features/auth/guards";
import { useMe } from "@/features/auth/session";
import { ActivePatientProvider, ModeProvider, useAccess } from "@/features/patient/context";
import { ReminderPrompt } from "@/features/reminders/ReminderPrompt";

import { useCaregiving } from "./api";

const icon = "size-5";

/** Each page appears only when the caregiver holds the permission it needs. */
const PERSON_PAGES: { path: string; label: string; icon: ReactNode; needs: string | null }[] = [
  { path: "", label: "Overview", icon: <LayoutDashboard className={icon} aria-hidden />, needs: null },
  { path: "health", label: "Health summary", icon: <HeartPulse className={icon} aria-hidden />, needs: "view_profile" },
  { path: "timeline", label: "Timeline", icon: <GanttChart className={icon} aria-hidden />, needs: null },
  { path: "medications", label: "Medicines", icon: <Pill className={icon} aria-hidden />, needs: "view_medications" },
  { path: "prescriptions", label: "Prescriptions", icon: <FileText className={icon} aria-hidden />, needs: "view_prescriptions" },
  { path: "history", label: "Medical history", icon: <History className={icon} aria-hidden />, needs: "view_medical_history" },
  { path: "visits", label: "Doctor visits", icon: <Stethoscope className={icon} aria-hidden />, needs: "view_visits" },
  { path: "tests", label: "Tests & reports", icon: <FlaskConical className={icon} aria-hidden />, needs: "view_reports" },
  { path: "appointments", label: "Appointments", icon: <CalendarDays className={icon} aria-hidden />, needs: "view_appointments" },
  { path: "emergency", label: "Emergency profile", icon: <Siren className={icon} aria-hidden />, needs: "manage_emergency_info" },
  { path: "reminders", label: "Reminder settings", icon: <Bell className={icon} aria-hidden />, needs: "manage_reminders" },
  { path: "caregivers", label: "Caregivers", icon: <Users className={icon} aria-hidden />, needs: "manage_caregivers" },
];

function PersonSwitcher({ current }: { current: string | undefined }) {
  const links = useCaregiving();
  const navigate = useNavigate();
  const people = (links.data ?? []).filter((l) => l.status === "active");
  if (people.length === 0) return null;
  return (
    <div className="px-1">
      <label htmlFor="person-switcher" className="mb-1 block text-xs font-medium text-muted">
        Viewing
      </label>
      <Select
        id="person-switcher"
        value={current ?? ""}
        onChange={(e) => void navigate(e.target.value ? `/care/${e.target.value}` : "/care")}
      >
        <option value="">Everyone</option>
        {people.map((p) => (
          <option key={p.patient_id} value={p.patient_id}>
            {p.patient_name ?? "Unnamed"}
          </option>
        ))}
      </Select>
    </div>
  );
}

function usePersonNav(patientId: string | undefined): NavItem[] {
  const access = useAccess(patientId ?? "");
  if (!patientId || !access.data) return [];
  const perms = new Set(access.data.permissions);
  return PERSON_PAGES.filter((p) => p.needs === null || perms.has(p.needs)).map((p) => ({
    to: `/care/${patientId}${p.path ? `/${p.path}` : ""}`,
    label: p.label,
    icon: p.icon,
    end: p.path === "",
  }));
}

export function CareLayout() {
  return (
    <RequireRole>
      <ModeProvider mode="caregiver">
        <CareShell />
      </ModeProvider>
    </RequireRole>
  );
}

function CareShell() {
  const me = useMe();
  const { patientId } = useParams();
  const links = useCaregiving();
  const personNav = usePersonNav(patientId);
  const person = links.data?.find((l) => l.patient_id === patientId && l.status === "active");

  const nav: NavItem[] = [
    { to: "/care", label: "Everyone", icon: <HeartHandshake className={icon} aria-hidden />, end: true },
    ...personNav,
    ...(me.patient_profile_id ? [{ to: "/patient", label: "My own health", icon: <UserRound className={icon} aria-hidden /> }] : []),
  ];

  return (
    <PortalShell
      portalName="Caregiver"
      nav={nav}
      switcher={<PersonSwitcher current={patientId} />}
      banner={
        person && (
          <p className="mb-4 rounded-lg bg-surface-2 px-3 py-2 text-sm" role="status">
            You are viewing <strong>{person.patient_name}</strong>’s record as their {person.is_guardian ? "guardian" : "caregiver"}.
            Doctor records are read-only.
          </p>
        )
      }
    >
      <Outlet />
    </PortalShell>
  );
}

/** Wraps a person's pages so they read that person's id, permissions and link base. */
export function CarePersonOutlet() {
  const { patientId = "" } = useParams();
  return (
    <ActivePatientProvider key={patientId} patientId={patientId} mode="caregiver" base={`/care/${patientId}`}>
      <Outlet />
      <ReminderPrompt />
    </ActivePatientProvider>
  );
}
