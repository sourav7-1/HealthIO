import createClient, { type ClientOptions } from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths };
export type Problem = {
  type: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
  request_id?: string;
  errors?: { loc: (string | number)[]; msg: string; type: string }[];
};

/** Typed Health Io API client. Types are generated from the FastAPI OpenAPI schema. */
export function createApiClient(options: ClientOptions = {}) {
  return createClient<paths>({ credentials: "include", ...options });
}

export type ApiClient = ReturnType<typeof createApiClient>;
