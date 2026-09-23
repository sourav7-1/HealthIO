import { LayoutDashboard, Users } from "lucide-react";
import { createBrowserRouter, Navigate, Outlet } from "react-router";

import { PortalShell } from "@/components/layout/PortalShell";
import { AccountPage } from "@/features/auth/AccountPage";
import { RequireRole } from "@/features/auth/guards";
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
        path: "patients/:patientId/:tab?",
        lazy: async () => ({ Component: (await import("@/features/doctor/PatientChartPage")).PatientChartPage }),
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
