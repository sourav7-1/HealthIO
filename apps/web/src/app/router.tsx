import {
  CalendarDays,
  ClipboardList,
  FileText,
  FlaskConical,
  HeartHandshake,
  HeartPulse,
  History,
  LayoutDashboard,
  Pill,
  Settings,
  Siren,
  Stethoscope,
  Sun,
  Users,
} from "lucide-react";
import { createBrowserRouter, Navigate, Outlet } from "react-router";

import { PortalShell } from "@/components/layout/PortalShell";
import { AccountPage } from "@/features/auth/AccountPage";
import { RequireRole } from "@/features/auth/guards";
import { useMe } from "@/features/auth/session";
import { CareLayout, CarePersonOutlet } from "@/features/care/CareLayout";
import { ActivePatientProvider } from "@/features/patient/context";
import { LoginPage } from "@/features/auth/LoginPage";

import { RouteError } from "./RouteError";

function DoctorLayout() {
  return (
    <RequireRole roles={["doctor"]}>
      <PortalShell
        portalName="Doctor portal"
        nav={[
          { to: "/doctor", label: "Dashboard", icon: <LayoutDashboard className="size-5" aria-hidden />, end: true },
          { to: "/doctor/patients", label: "Patients", icon: <Users className="size-5" aria-hidden /> },
        ]}
      >
        <Outlet />
      </PortalShell>
    </RequireRole>
  );
}

const icon = "size-5";

function PatientLayout() {
  return (
    <RequireRole roles={["patient"]}>
      <PatientShell />
    </RequireRole>
  );
}

function PatientShell() {
  const me = useMe();
  if (!me.patient_profile_id) return <Navigate to="/account" replace />;
  return (
    <ActivePatientProvider patientId={me.patient_profile_id} mode="self" base="/patient">
      <PortalShell
        portalName="My health"
        nav={[
          { to: "/patient", label: "Today", icon: <Sun className={icon} aria-hidden />, end: true },
          { to: "/patient/health", label: "My health", icon: <HeartPulse className={icon} aria-hidden /> },
          { to: "/patient/medications", label: "Medicines", icon: <Pill className={icon} aria-hidden /> },
          { to: "/patient/prescriptions", label: "Prescriptions", icon: <FileText className={icon} aria-hidden /> },
          { to: "/patient/history", label: "Medical history", icon: <History className={icon} aria-hidden /> },
          { to: "/patient/visits", label: "Doctor visits", icon: <Stethoscope className={icon} aria-hidden /> },
          { to: "/patient/tests", label: "Tests & reports", icon: <FlaskConical className={icon} aria-hidden /> },
          { to: "/patient/appointments", label: "Appointments", icon: <CalendarDays className={icon} aria-hidden /> },
          { to: "/patient/emergency", label: "Emergency profile", icon: <Siren className={icon} aria-hidden /> },
          { to: "/patient/caregivers", label: "Caregivers", icon: <ClipboardList className={icon} aria-hidden /> },
          { to: "/patient/settings", label: "Settings", icon: <Settings className={icon} aria-hidden /> },
          { to: "/care", label: "People I care for", icon: <HeartHandshake className={icon} aria-hidden /> },
        ]}
      >
        <Outlet />
      </PortalShell>
    </ActivePatientProvider>
  );
}

const records = () => import("@/features/patient/pages/RecordsPages");
const profile = () => import("@/features/patient/pages/ProfilePages");
const medications = () => import("@/features/patient/pages/MedicationsPage");
const care = () => import("@/features/care/pages");
const scans = () => import("@/features/scans/ScanReviewPage");

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage />, errorElement: <RouteError /> },
  {
    path: "/doctor",
    element: <DoctorLayout />,
    errorElement: <RouteError />,
    children: [
      // Portal pages load on demand: other roles never download doctor code.
      { index: true, lazy: async () => ({ Component: (await import("@/features/doctor/DashboardPage")).DashboardPage }) },
      { path: "patients", lazy: async () => ({ Component: (await import("@/features/doctor/PatientsPage")).PatientsPage }) },
      {
        path: "patients/:patientId/visits/:visitId",
        lazy: async () => ({ Component: (await import("@/features/doctor/VisitPage")).VisitPage }),
      },
      {
        path: "patients/:patientId/prescriptions/scan/:scanId",
        lazy: async () => ({ Component: (await import("@/features/doctor/ScanPage")).DoctorScanPage }),
      },
      {
        path: "patients/:patientId/prescriptions/:prescriptionId",
        lazy: async () => ({ Component: (await import("@/features/doctor/PrescriptionPage")).PrescriptionPage }),
      },
      {
        path: "patients/:patientId/:tab?",
        lazy: async () => ({ Component: (await import("@/features/doctor/PatientChartPage")).PatientChartPage }),
      },
    ],
  },
  {
    path: "/patient",
    element: <PatientLayout />,
    errorElement: <RouteError />,
    children: [
      { index: true, lazy: async () => ({ Component: (await import("@/features/patient/pages/TodayPage")).TodayPage }) },
      { path: "medications", lazy: async () => ({ Component: (await medications()).MedicationsPage }) },
      { path: "health", lazy: async () => ({ Component: (await profile()).MyHealthPage }) },
      { path: "history", lazy: async () => ({ Component: (await records()).HistoryPage }) },
      { path: "visits", lazy: async () => ({ Component: (await records()).VisitsPage }) },
      { path: "visits/:visitId", lazy: async () => ({ Component: (await records()).VisitDetailPage }) },
      { path: "prescriptions", lazy: async () => ({ Component: (await records()).PrescriptionsPage }) },
      { path: "prescriptions/scan/:scanId", lazy: async () => ({ Component: (await scans()).ScanReviewPage }) },
      { path: "prescriptions/:prescriptionId", lazy: async () => ({ Component: (await records()).PrescriptionDetailPage }) },
      { path: "tests", lazy: async () => ({ Component: (await records()).TestsPage }) },
      { path: "appointments", lazy: async () => ({ Component: (await records()).AppointmentsPage }) },
      { path: "emergency", lazy: async () => ({ Component: (await profile()).EmergencyPage }) },
      { path: "caregivers", lazy: async () => ({ Component: (await profile()).CaregiversPage }) },
      { path: "settings", lazy: async () => ({ Component: (await profile()).SettingsPage }) },
    ],
  },
  {
    path: "/care",
    element: <CareLayout />,
    errorElement: <RouteError />,
    children: [
      { index: true, lazy: async () => ({ Component: (await care()).CareDashboardPage }) },
      {
        path: ":patientId",
        element: <CarePersonOutlet />,
        children: [
          // The same pages as the patient portal; they read who and what from context.
          { index: true, lazy: async () => ({ Component: (await care()).CarePersonPage }) },
          { path: "health", lazy: async () => ({ Component: (await profile()).MyHealthPage }) },
          { path: "medications", lazy: async () => ({ Component: (await medications()).MedicationsPage }) },
          { path: "prescriptions", lazy: async () => ({ Component: (await records()).PrescriptionsPage }) },
          { path: "prescriptions/scan/:scanId", lazy: async () => ({ Component: (await scans()).ScanReviewPage }) },
      { path: "prescriptions/:prescriptionId", lazy: async () => ({ Component: (await records()).PrescriptionDetailPage }) },
          { path: "history", lazy: async () => ({ Component: (await records()).HistoryPage }) },
          { path: "visits", lazy: async () => ({ Component: (await records()).VisitsPage }) },
          { path: "visits/:visitId", lazy: async () => ({ Component: (await records()).VisitDetailPage }) },
          { path: "tests", lazy: async () => ({ Component: (await records()).TestsPage }) },
          { path: "appointments", lazy: async () => ({ Component: (await records()).AppointmentsPage }) },
          { path: "emergency", lazy: async () => ({ Component: (await profile()).EmergencyPage }) },
          { path: "caregivers", lazy: async () => ({ Component: (await profile()).CaregiversPage }) },
          { path: "reminders", lazy: async () => ({ Component: (await care()).CareRemindersPage }) },
        ],
      },
    ],
  },
  {
    path: "/account",
    element: (
      <RequireRole>
        <AccountPage />
      </RequireRole>
    ),
  },
  { path: "/", element: <Navigate to="/login" replace /> },
  { path: "*", element: <RouteError notFound /> },
]);
