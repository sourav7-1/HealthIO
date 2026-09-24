/**
 * The person whose record is on screen: the signed-in patient themselves, or someone a
 * caregiver looks after. Pages read the patient id, link base and permissions from here,
 * so the same pages serve both portals. Permissions only shape the UI; the API enforces them.
 */
import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useMemo, type ReactNode } from "react";

import { ErrorState, Spinner } from "@/components/ui";
import { keys } from "@/features/chart/api";
import { api, errorMessage, unwrap } from "@/lib/api";

export type Mode = "self" | "caregiver";

export interface ActivePatient {
  patientId: string;
  mode: Mode;
  /** Link prefix for this person's pages, e.g. "/patient" or "/care/<id>". */
  base: string;
  name: string | null;
  permissions: ReadonlySet<string>;
  can: (permission: string) => boolean;
}

const Ctx = createContext<ActivePatient | null>(null);
/** Portal-wide voice ("your doctor" vs "a doctor") for views that span several people. */
const ModeCtx = createContext<Mode>("self");

export function ModeProvider({ mode, children }: { mode: Mode; children: ReactNode }) {
  return <ModeCtx.Provider value={mode}>{children}</ModeCtx.Provider>;
}

export function useAccess(patientId: string, enabled = true) {
  return useQuery({
    queryKey: [...keys.patient(patientId), "access"],
    enabled: enabled && patientId !== "",
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/access", { params: { path: { patient_id: patientId } } })),
    staleTime: 30_000,
  });
}

export function ActivePatientProvider({
  patientId,
  mode,
  base,
  name = null,
  children,
}: {
  patientId: string;
  mode: Mode;
  base: string;
  name?: string | null;
  children: ReactNode;
}) {
  const access = useAccess(patientId);
  const value = useMemo<ActivePatient | null>(() => {
    if (!access.data) return null;
    const permissions = new Set(access.data.permissions);
    return { patientId, mode, base, name, permissions, can: (p) => permissions.has(p) };
  }, [access.data, patientId, mode, base, name]);

  if (access.isPending) return <Spinner label="Loading" />;
  if (access.isError || !value) {
    return <ErrorState message={errorMessage(access.error)} onRetry={() => void access.refetch()} />;
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useActivePatient(): ActivePatient {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useActivePatient must be used inside ActivePatientProvider");
  return ctx;
}

/** Safe outside an ActivePatientProvider: falls back to the portal's mode, then "self". */
export function useMode(): Mode {
  const portal = useContext(ModeCtx);
  return useContext(Ctx)?.mode ?? portal;
}

export function usePatientId(): string {
  return useActivePatient().patientId;
}
