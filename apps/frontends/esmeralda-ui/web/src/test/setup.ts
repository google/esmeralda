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

// Typed test fixtures (synthetic names only) and a fetch stub that routes by URL.
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import type { Catalog, Me, Trace, TraceList, Usage } from "../api/client";

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

export const ME: Me = {
  email: "julian.sterling@example.com",
  subject: "accounts.google.com:1",
  auth: "iap",
  environment: "dev",
  demo: false,
  data_source: "bigquery",
};

export const CATALOG: Catalog = {
  environment: "dev",
  resolved_file: "catalog/.resolved/dev.json",
  resolved_sha256: "a".repeat(64),
  policy: { unmapped_caller: "deny", declared_mismatch: "reject" },
  teams: [
    { name: "ai-coe", cost_centers: ["cc-ai-coe"], external: false },
    { name: "cx", cost_centers: ["cc-cx"], external: false },
  ],
  targets: [
    {
      name: "income-verification",
      kind: "mcp",
      owner_team: "ai-coe",
      description: "Verifies applicant income (synthetic data).",
      example: false,
      deploy: "services/income-verification",
      compiled: true,
      registry: null,
      gateway: "agent-gateway",
      ingress_backend: "direct",
      callers: [
        {
          caller: "ai-coe-mortgage-specialist",
          identity: "workload (agent)",
          team: "ai-coe",
          gateway: "agent-gateway",
          pays: { team: "ai-coe", cost_centers: ["cc-ai-coe"] },
          enforced: true,
        },
      ],
    },
    {
      name: "cx-mortgage-orchestrator",
      kind: "adk",
      owner_team: "cx",
      description: '<img src="x" data-testid="injected">',
      example: false,
      deploy: "agents/cx-mortgage-orchestrator",
      compiled: true,
      registry: null,
      gateway: null,
      ingress_backend: "direct",
      callers: [],
    },
  ],
};

export const USAGE: Usage = {
  source: "bigquery",
  month: "2026-10",
  team: null,
  rows: [
    {
      billing_month: "2026-10-01T00:00:00Z",
      agent_id: "cx-mortgage-orchestrator",
      model: "gemini-flash",
      billed_team: "cx",
      verified: true,
      attribution: "verified",
      workload_team: "cx",
      total_requests: 12,
      total_llm_calls: 30,
      total_tokens: 4200,
      cached_prompt_tokens: 100,
      cache_hit_ratio_pct: 2.4,
      net_total_chargeback_usd: 0.42,
      gateway_metered_requests: 12,
    },
  ],
  totals: [{ billed_team: "cx", total_requests: 12, total_tokens: 4200, net_total_chargeback_usd: 0.42 }],
};

export const TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736";

export const TRACES: TraceList = {
  source: "demo",
  days: 1,
  traces: [
    {
      trace_id: TRACE_ID,
      started_at: "2026-11-01T12:00:00Z",
      agents: ["cx-mortgage-orchestrator"],
      billed_team: "cx",
      total_tokens: 15,
    },
  ],
};

const span = {
  timestamp: "2026-11-01T12:00:02Z",
  event: "llm.call",
  source: "workload",
  span_id: null,
  workload_name: "cx-mortgage-orchestrator",
  workload_team: "cx",
  caller_principal: null,
  caller_team: null,
  caller_verified: true,
  model: "gemini-flash",
  input_tokens: 10,
  output_tokens: 5,
  tool_name: null,
  tool_server: null,
  tool_status: null,
  gateway_product: null,
  gateway_name: null,
  http_status_code: null,
  status: null,
  error_type: null,
};

export const TRACE: Trace = {
  source: "demo",
  trace_id: TRACE_ID,
  started_at: "2026-11-01T12:00:00Z",
  duration_ms: 2000,
  total_tokens: 15,
  spans: [
    { ...span, offset_ms: 0, duration_ms: 2000, event: "gateway.request", source: "gateway", gateway_product: "agent-gateway", model: null },
    { ...span, offset_ms: 500, duration_ms: 500 },
  ],
};

type Routes = Record<string, { status?: number; body: unknown }>;

/** Stubs fetch: the longest matching path prefix of `routes` answers; unknown paths are 404. */
export function stubFetch(routes: Routes) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    calls.push(url);
    const path = url.split("?")[0] ?? url;
    const key = Object.keys(routes)
      .filter((k) => path === k || path.startsWith(`${k}/`) || url === k)
      .sort((a, b) => b.length - a.length)[0];
    const route = key ? routes[key] : undefined;
    const status = route?.status ?? (route ? 200 : 404);
    const body = route ? route.body : { error: "not found" };
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}
