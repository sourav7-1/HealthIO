import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { Alert, Spinner } from "@/components/ui";
import type { Schemas } from "@/lib/api";

import { useSession } from "./session";

type Role = Schemas["Role"];

export function homeFor(me: Schemas["MeResponse"]): string {
  if (me.roles.includes("doctor")) return "/doctor";
  return "/account";
}

/** Gate a route on being signed in (and optionally holding one of the roles).
 * This only shapes the UI: every API call is authorised by the server regardless. */
export function RequireRole({ roles, children }: { roles?: Role[]; children: ReactNode }) {
  const { state } = useSession();
  const location = useLocation();

  if (state.status === "loading") return <Spinner label="Loading your session" />;
  if (state.status === "signed-out") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (!state.me.email_verified) {
    return (
      <div className="mx-auto max-w-lg p-6">
        <Alert tone="warning" title="Verify your email">
          We sent a confirmation link to your email address. Open it to finish setting up your account.
        </Alert>
      </div>
    );
  }
  if (roles && !roles.some((r) => state.me.roles.includes(r))) {
    return <Navigate to={homeFor(state.me)} replace />;
  }
  return <>{children}</>;
}
