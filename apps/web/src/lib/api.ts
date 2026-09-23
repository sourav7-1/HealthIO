/**
 * Typed API client (generated from the backend OpenAPI schema) with session handling.
 *
 * - The access token lives only in memory (never localStorage).
 * - The refresh token is an httpOnly cookie the page cannot read; `refreshSession()`
 *   exchanges it for a new access token (with the anti-CSRF header the API requires).
 * - A 401 on any call triggers one refresh (shared by concurrent requests) and a retry.
 */
import { createApiClient, type components, type Problem } from "@health-io/api-client";

export type Schemas = components["schemas"];

let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;
const listeners = new Set<(signedIn: boolean) => void>();

export function setAccessToken(token: string | null): void {
  accessToken = token;
  listeners.forEach((l) => l(token !== null));
}

export function onSessionChange(listener: (signedIn: boolean) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function refreshSession(): Promise<boolean> {
  refreshInFlight ??= (async () => {
    try {
      const res = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-Requested-With": "healthio" },
      });
      if (!res.ok) {
        setAccessToken(null);
        return false;
      }
      const body = (await res.json()) as { access_token: string };
      setAccessToken(body.access_token);
      return true;
    } catch {
      setAccessToken(null);
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

const AUTH_PATHS = ["/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/register"];

async function authFetch(input: Request): Promise<Response> {
  const withToken = (req: Request) => {
    const copy = req.clone();
    if (accessToken) copy.headers.set("Authorization", `Bearer ${accessToken}`);
    return fetch(copy);
  };
  const response = await withToken(input);
  const isAuthCall = AUTH_PATHS.some((p) => new URL(input.url).pathname === p);
  if (response.status !== 401 || isAuthCall || accessToken === null) return response;
  return (await refreshSession()) ? withToken(input) : response;
}

export const api = createApiClient({
  baseUrl: typeof window === "undefined" ? "http://localhost" : window.location.origin,
  fetch: authFetch,
  credentials: "same-origin",
});

/** An API error carrying the RFC 9457 problem document. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem | undefined;

  constructor(status: number, problem?: Problem) {
    super(problem?.detail ?? problem?.title ?? `Request failed (${status})`);
    this.status = status;
    this.problem = problem;
  }

  get code(): string | undefined {
    return this.problem?.type.split("/").pop();
  }

  /** Field-level validation messages, keyed by the last path segment. */
  fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const e of this.problem?.errors ?? []) {
      const key = String(e.loc[e.loc.length - 1] ?? "");
      if (key && !(key in out)) out[key] = e.msg;
    }
    return out;
  }
}

/** Unwrap an openapi-fetch result: return data or throw ApiError. */
export async function unwrap<T>(
  promise: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) throw new ApiError(response.status, error as Problem | undefined);
  return data as T;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403 && err.code === "forbidden") {
      return err.problem?.detail ?? "You do not have permission to do this.";
    }
    if (err.status === 404) return "Not found, or you do not have access.";
    if (err.status >= 500) return "Something went wrong on our side. Please try again.";
    return err.message;
  }
  if (err instanceof TypeError) return "Cannot reach the server. Check your connection.";
  return "Something went wrong. Please try again.";
}
