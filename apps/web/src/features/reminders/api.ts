import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { keys } from "@/features/chart/api";
import { api, unwrap, type Schemas } from "@/lib/api";

export const REMINDER_TITLE = "Time for your medication";

export type Reminder = Schemas["ReminderOut"];
export type Inbox = Schemas["InboxOut"];

/** Doses to take now. Polled so reminders work even without Web Push or a worker. */
export function useDueReminders(pid: string, enabled = true) {
  return useQuery({
    queryKey: [...keys.section(pid, "reminders"), "due"],
    enabled,
    queryFn: () => unwrap(api.GET("/api/v1/patients/{patient_id}/reminders/due", { params: { path: { patient_id: pid } } })),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}

export function useMissedReminders(pid: string, enabled = true) {
  return useQuery({
    queryKey: [...keys.section(pid, "reminders"), "missed"],
    enabled,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/patients/{patient_id}/reminders/missed", {
          params: { path: { patient_id: pid }, query: { hours: 24 } },
        }),
      ),
    refetchInterval: 5 * 60_000,
  });
}

export function useInbox() {
  return useQuery({
    queryKey: ["me", "notifications"],
    queryFn: () => unwrap(api.GET("/api/v1/me/notifications")),
    refetchInterval: 60_000,
  });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string | null) =>
      id
        ? unwrap(api.POST("/api/v1/me/notifications/{notification_id}/read", { params: { path: { notification_id: id } } }))
        : unwrap(api.POST("/api/v1/me/notifications/read-all")),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me", "notifications"] }),
  });
}
