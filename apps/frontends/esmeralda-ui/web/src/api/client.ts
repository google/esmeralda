// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Typed access to the BFF. Every type comes from schema.ts (generated from openapi.json), so the UI
// can't use a field the API doesn't have. Same-origin only: IAP's cookie authenticates the browser,
// the BFF verifies the IAP JWT, and this code never handles credentials.
import type { components, paths } from "./schema";

type Schemas = components["schemas"];
export type Me = Schemas["Me"];
export type Catalog = Schemas["Catalog"];
export type CatalogTarget = Schemas["CatalogTarget"];
export type CatalogCaller = Schemas["CatalogCaller"];
export type Usage = Schemas["Usage"];
export type ChargebackRow = Schemas["ChargebackRow"];
export type Audit = Schemas["Audit"];
export type TraceList = Schemas["TraceList"];
export type Trace = Schemas["Trace"];
export type TraceSpan = Schemas["TraceSpan"];

type JsonOf<P extends keyof paths> = paths[P] extends {
  get: { responses: { 200: { content: { "application/json": infer T } } } };
}
  ? T
  : never;

export const TRACE_ID_RE = /^[0-9a-f]{32}$/;
export const MONTH_RE = /^[0-9]{4}-(0[1-9]|1[0-2])$/;

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<P extends keyof paths>(
  path: P,
  url: string = path,
  signal?: AbortSignal,
): Promise<JsonOf<P>> {
  const response = await fetch(url, {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    ...(signal ? { signal } : {}),
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "error" in body && typeof body.error === "string") {
        message = body.error;
      }
    } catch {
      // keep the status line
    }
    throw new ApiError(response.status, message);
  }
  return (await response.json()) as JsonOf<P>;
}

function withQuery(base: string, params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `${base}?${text}` : base;
}

export const api = {
  me: (signal?: AbortSignal) => getJson("/api/me", "/api/me", signal),
  catalog: (signal?: AbortSignal) => getJson("/api/catalog", "/api/catalog", signal),
  usage: (month: string | undefined, team: string | undefined, signal?: AbortSignal) =>
    getJson("/api/usage", withQuery("/api/usage", { month, team }), signal),
  audit: (days: number, signal?: AbortSignal) => getJson("/api/audit", withQuery("/api/audit", { days }), signal),
  traces: (days: number, signal?: AbortSignal) =>
    getJson("/api/traces", withQuery("/api/traces", { days }), signal),
  trace: (traceId: string, signal?: AbortSignal) => {
    if (!TRACE_ID_RE.test(traceId)) {
      return Promise.reject(new ApiError(422, "a trace id is 32 lowercase hex characters"));
    }
    return getJson("/api/traces/{trace_id}", `/api/traces/${traceId}`, signal);
  },
};
