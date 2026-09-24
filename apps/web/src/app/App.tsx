import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router";

import { HealthIOSplash, ToastProvider } from "@/components/ui";
import { SessionProvider } from "@/features/auth/session";
import { ApiError } from "@/lib/api";

import { router } from "./router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: true,
      // Retrying a 4xx (not found, forbidden, validation) never helps.
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
    },
  },
});

export function App() {
  const [showSplash, setShowSplash] = useState(() => {
    try {
      if (typeof window === "undefined") return false;
      if (window.location.pathname === "/splash") return false;
      return !sessionStorage.getItem("hio_splash_seen");
    } catch {
      return false;
    }
  });

  const handleSplashComplete = () => {
    try {
      sessionStorage.setItem("hio_splash_seen", "1");
    } catch {
      // Ignore private mode quota errors
    }
    setShowSplash(false);
  };

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <SessionProvider>
          {showSplash && <HealthIOSplash onComplete={handleSplashComplete} />}
          <RouterProvider router={router} />
        </SessionProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
