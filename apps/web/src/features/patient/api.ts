/** Data hooks used only by the patient portal. Chart reads are shared from features/chart. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { keys } from "@/features/chart/api";
import { api, unwrap, type Schemas } from "@/lib/api";

export type Dose = Schemas["DoseOut"];
export type MedicationOut = Schemas["MedicationOut"];
export type ReminderPreferences = Schemas["PreferencesModel"];
export type EmergencyProfile = Schemas["EmergencyProfileOut"];
export type EmergencyContact = Schemas["ContactOut"];
export type CaregiverLink = Schemas["CaregiverLinkOut"];
export type ConnectionRequest = Schemas["ConnectionRequestOut"];

const path = (patient_id: string) => ({ params: { path: { patient_id } } });

function useInvalidatePatient(pid: string) {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: keys.patient(pid) });
}

// --- doses and medicines -------------------------------------------------------------------

export function useDoses(pid: string, days = 1) {
  return useQuery({
    queryKey: [...keys.section(pid, "doses"), days],
    queryFn: () =>
      unwrap(api.GET("/api/v1/patients/{patient_id}/doses", { params: { path: { patient_id: pid }, query: { days } } })),
    refetchInterval: 60_000, // statuses change with time (due → missed)
  });
}

export function useDoseAction(pid: string) {
  const qc = useQueryClient();
  const invalidatePatient = useInvalidatePatient(pid);
  // Caregivers act from their dashboard too; refresh it along with the patient's data.
  const invalidate = () => Promise.all([invalidatePatient(), qc.invalidateQueries({ queryKey: ["me", "caregiving"] })]);
  return useMutation({
    mutationFn: ({ dose_id, action, reason, minutes }: {
      dose_id: string;
      action: "take" | "skip" | "snooze";
      reason?: string;
      minutes?: 5 | 10 | 15 | 30 | 60;
    }) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/doses/{dose_id}/{action}", {
          params: { path: { patient_id: pid, dose_id, action } },
          body: { reason: reason ?? null, minutes: minutes ?? null },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useLogAsNeeded(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (medication_id: string) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/medications/{medication_id}/as-needed-dose", {
          params: { path: { patient_id: pid, medication_id } },
          body: {},
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useConfirmMedication(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: ({ medication_id, ...body }: Schemas["ReminderTimesIn"] & { medication_id: string }) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/medications/{medication_id}/confirm", {
          params: { path: { patient_id: pid, medication_id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useChangeReminderTimes(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: ({ medication_id, ...body }: Schemas["ReminderTimesIn"] & { medication_id: string }) =>
      unwrap(
        api.PUT("/api/v1/patients/{patient_id}/medications/{medication_id}/reminder-times", {
          params: { path: { patient_id: pid, medication_id } },
          body,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useAddSelfReported(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (body: Schemas["SelfReportedIn"]) =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/medications/self-reported", { ...path(pid), body })),
    onSuccess: invalidate,
  });
}

export function useStopMedication(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: ({ medication_id, reason }: { medication_id: string; reason: string | null }) =>
      unwrap(
        api.POST("/api/v1/patients/{patient_id}/medications/{medication_id}/stop", {
          params: { path: { patient_id: pid, medication_id } },
          body: { reason },
        }),
      ),
    onSuccess: invalidate,
  });
}

// --- self-reported history -------------------------------------------------------------------

export function useReportAllergy(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (body: Schemas["ReportedAllergyIn"]) =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/self-reported/allergies", { ...path(pid), body })),
    onSuccess: invalidate,
  });
}

export function useReportCondition(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (body: Schemas["ReportedConditionIn"]) =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/self-reported/conditions", { ...path(pid), body })),
    onSuccess: invalidate,
  });
}

export function useRemoveSelfReported(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: ({ kind, entry_id }: { kind: "allergies" | "conditions"; entry_id: string }) =>
      unwrap(
        api.DELETE("/api/v1/patients/{patient_id}/self-reported/{kind}/{entry_id}", {
          params: { path: { patient_id: pid, kind, entry_id } },
        }),
      ),
    onSuccess: invalidate,
  });
}

// --- profile, emergency, reminders -------------------------------------------------------------

export function useProfile(pid: string, enabled = true) {
  return useQuery({
    queryKey: keys.section(pid, "profile"),
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/profile", path(pid))),
  });
}

export function useUpdateProfile(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (body: Schemas["ProfileUpdate"]) =>
      unwrap(api.PATCH("/api/v1/patients/{patient_id}/profile", { ...path(pid), body })),
    onSuccess: invalidate,
  });
}

export function useReminderPreferences(pid: string) {
  return useQuery({
    queryKey: keys.section(pid, "reminder-preferences"),
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/reminder-preferences", path(pid))),
  });
}

export function useSaveReminderPreferences(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (body: ReminderPreferences) =>
      unwrap(api.PUT("/api/v1/patients/{patient_id}/reminder-preferences", { ...path(pid), body })),
    onSuccess: invalidate,
  });
}

export function useEmergencyProfile(pid: string) {
  return useQuery({
    queryKey: keys.section(pid, "emergency"),
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/emergency-profile", path(pid))),
  });
}

export function useSaveEmergencyProfile(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (body: Schemas["EmergencyProfileModel"]) =>
      unwrap(api.PUT("/api/v1/patients/{patient_id}/emergency-profile", { ...path(pid), body })),
    onSuccess: invalidate,
  });
}

export function useEmergencyContacts(pid: string) {
  return useQuery({
    queryKey: keys.section(pid, "emergency-contacts"),
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/emergency-contacts", path(pid))),
  });
}

export function useAddEmergencyContact(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (body: Schemas["ContactIn"]) =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/emergency-contacts", { ...path(pid), body })),
    onSuccess: invalidate,
  });
}

export function useRemoveEmergencyContact(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (contact_id: string) =>
      unwrap(
        api.DELETE("/api/v1/patients/{patient_id}/emergency-contacts/{contact_id}", {
          params: { path: { patient_id: pid, contact_id } },
        }),
      ),
    onSuccess: invalidate,
  });
}

// --- doctors and caregivers ---------------------------------------------------------------------

export function useDoctorRequests(enabled = true) {
  return useQuery({
    queryKey: ["me", "doctor-requests"],
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/me/doctor-requests")),
  });
}

export function useRespondToDoctor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ relationship_id, decision, data_categories }: {
      relationship_id: string;
      decision: "accept" | "decline";
      data_categories: Schemas["DataCategory"][];
    }) =>
      unwrap(
        api.POST("/api/v1/me/doctor-requests/{relationship_id}/{decision}", {
          params: { path: { relationship_id, decision } },
          body: { data_categories },
        }),
      ),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useCaregivers(pid: string) {
  return useQuery({
    queryKey: keys.section(pid, "caregivers"),
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/caregivers", path(pid))),
  });
}

export function useInviteCaregiver(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (body: Schemas["InviteCaregiver"]) =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/caregivers", { ...path(pid), body })),
    onSuccess: invalidate,
  });
}

export function useSetCaregiverScopes(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: ({ relationship_id, scopes }: { relationship_id: string; scopes: Schemas["CaregiverPermissionScope"][] }) =>
      unwrap(
        api.PUT("/api/v1/patients/{patient_id}/caregivers/{relationship_id}/scopes", {
          params: { path: { patient_id: pid, relationship_id } },
          body: { scopes },
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useCaregiverActivity(pid: string) {
  return useQuery({
    queryKey: keys.section(pid, "caregiver-activity"),
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/caregivers/activity", path(pid))),
  });
}

export function useRevokeCaregiver(pid: string) {
  const invalidate = useInvalidatePatient(pid);
  return useMutation({
    mutationFn: (relationship_id: string) =>
      unwrap(
        api.DELETE("/api/v1/patients/{patient_id}/caregivers/{relationship_id}", {
          params: { path: { patient_id: pid, relationship_id } },
        }),
      ),
    onSuccess: invalidate,
  });
}

// --- account ---------------------------------------------------------------------------------------

export function useUpdateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["AccountUpdate"]) => unwrap(api.PATCH("/api/v1/me", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      unwrap(api.POST("/api/v1/auth/password/change", { body })),
  });
}

export function useSessions() {
  return useQuery({ queryKey: ["me", "sessions"], queryFn: () => unwrap(api.GET("/api/v1/auth/sessions")) });
}

export function useRevokeSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (session_id: string) =>
      unwrap(api.DELETE("/api/v1/auth/sessions/{session_id}", { params: { path: { session_id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me", "sessions"] }),
  });
}
