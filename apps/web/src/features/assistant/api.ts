/** AI health assistant: asking, saved conversations and privacy preferences. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { keys } from "@/features/chart/api";
import { api, unwrap, type Schemas } from "@/lib/api";

export type Answer = Schemas["AnswerOut"];
export type AnswerSegment = Schemas["SegmentOut"];
export type AnswerSource = Schemas["SourceOut"];
export type Conversation = Schemas["ConversationOut"];
export type AssistantPreferences = Schemas["PreferencesOut"];
export type Turn = Schemas["Turn"];

export const STARTERS = [
  "What medicines am I taking, and when?",
  "What did my last prescription say?",
  "When is my next appointment?",
  "What does my latest test report show?",
];

const chats = (pid: string) => [...keys.patient(pid), "assistant"] as const;

export function useAssistantPreferences() {
  return useQuery({
    queryKey: ["me", "assistant-preferences"],
    queryFn: () => unwrap(api.GET("/api/v1/me/assistant/preferences")),
  });
}

export function useUpdateAssistantPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["PreferencesIn"]) => unwrap(api.PUT("/api/v1/me/assistant/preferences", { body })),
    onSuccess: (data) => {
      qc.setQueryData(["me", "assistant-preferences"], data);
      void qc.invalidateQueries({ queryKey: ["patient"] });
    },
  });
}

export function useConversations(pid: string) {
  return useQuery({
    queryKey: [...chats(pid), "list"],
    queryFn: () =>
      unwrap(api.GET("/api/v1/patients/{patient_id}/assistant/conversations", { params: { path: { patient_id: pid } } })),
  });
}

export function useConversation(pid: string, conversationId: string | null) {
  return useQuery({
    queryKey: [...chats(pid), "conversation", conversationId],
    enabled: conversationId !== null,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/patients/{patient_id}/assistant/conversations/{conversation_id}", {
          params: { path: { patient_id: pid, conversation_id: conversationId! } },
        }),
      ),
  });
}

export function useAsk(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["AskIn"]) =>
      unwrap(api.POST("/api/v1/patients/{patient_id}/assistant/ask", { params: { path: { patient_id: pid } }, body })),
    onSuccess: (data) => {
      if (data.saved) void qc.invalidateQueries({ queryKey: chats(pid) });
    },
  });
}

export function useDeleteConversation(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (conversationId: string | null) =>
      conversationId
        ? unwrap(
            api.DELETE("/api/v1/patients/{patient_id}/assistant/conversations/{conversation_id}", {
              params: { path: { patient_id: pid, conversation_id: conversationId } },
            }),
          )
        : unwrap(
            api.DELETE("/api/v1/patients/{patient_id}/assistant/conversations", { params: { path: { patient_id: pid } } }),
          ),
    onSuccess: () => qc.invalidateQueries({ queryKey: chats(pid) }),
  });
}
