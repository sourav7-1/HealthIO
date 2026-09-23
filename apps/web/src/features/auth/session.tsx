import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  api,
  onSessionChange,
  refreshSession,
  setAccessToken,
  unwrap,
  type Schemas,
} from "@/lib/api";

type Me = Schemas["MeResponse"];

type SessionState =
  | { status: "loading" }
  | { status: "signed-out" }
  | { status: "signed-in"; me: Me };

interface SessionContextValue {
  state: SessionState;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [booted, setBooted] = useState(false);
  const [signedIn, setSignedIn] = useState(false);

  // Restore the session from the refresh cookie once, on load.
  useEffect(() => {
    const off = onSessionChange(setSignedIn);
    void refreshSession().finally(() => setBooted(true));
    return off;
  }, []);

  const me = useQuery({
    queryKey: ["me"],
    enabled: signedIn,
    queryFn: () => unwrap(api.GET("/api/v1/me")),
    staleTime: 60_000,
  });

  const signIn = useCallback(
    async (email: string, password: string) => {
      const body = await unwrap(api.POST("/api/v1/auth/login", { body: { email, password } }));
      setAccessToken(body.access_token);
      await queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    [queryClient],
  );

  const signOut = useCallback(async () => {
    try {
      await api.POST("/api/v1/auth/logout");
    } finally {
      setAccessToken(null);
      queryClient.clear(); // no patient data survives sign-out
    }
  }, [queryClient]);

  const state: SessionState = useMemo(() => {
    if (!booted || (signedIn && me.isPending)) return { status: "loading" };
    if (!signedIn || !me.data) return { status: "signed-out" };
    return { status: "signed-in", me: me.data };
  }, [booted, signedIn, me.isPending, me.data]);

  const value = useMemo(() => ({ state, signIn, signOut }), [state, signIn, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}

export function useMe(): Me {
  const { state } = useSession();
  if (state.status !== "signed-in") throw new Error("useMe requires a signed-in session");
  return state.me;
}
