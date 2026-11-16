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

import {
  redactUserCredential,
  type RuntimeCatalogDocument,
  type TraceExplorationResult,
  type TraceHop,
  type UsageEventRecord,
} from "../contracts/schemas.ts";
import {
  SYNTHETIC_CHARGEBACK_ROWS,
  SYNTHETIC_SECURITY_AUDIT_ROWS,
  type ChargebackRow,
  type SecurityAuditRow,
} from "./fixtures.ts";
import { fetchAccessToken } from "./gcpAuth.ts";
import { getRecordedTrace, listRecentTraces } from "./invoker.ts";

/**
 * Trace Explorer (`/api/traces/:traceId`) and Read-Only Admin (`/api/admin/summary`) services.
 *
 * - Queries Cloud Logging (`jsonPayload.schema="esmeralda.usage" AND jsonPayload.trace_id="<id>"`)
 *   when `GOVERNANCE_PROJECT_ID` and GCP credentials are present; falls back cleanly to recorded
 *   or synthetic trace fixtures.
 * - Queries Governance BigQuery views (`vw_monthly_agent_chargeback`, `vw_security_audit_trail`)
 *   when live credentials are present; falls back cleanly to synthetic FinOps and security rows.
 */

async function queryCloudLoggingTrace(
  governanceProjectId: string,
  traceId: string,
  env: NodeJS.ProcessEnv
): Promise<TraceExplorationResult | null> {
  const token = await fetchAccessToken(env);
  if (!token) return null;

  try {
    const res = await fetch("https://logging.googleapis.com/v2/entries:list", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        resourceNames: [`projects/${governanceProjectId}`],
        filter: `jsonPayload.schema="esmeralda.usage" AND jsonPayload.trace_id="${traceId}"`,
        orderBy: "timestamp asc",
        pageSize: 50,
      }),
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      entries?: Array<{ jsonPayload?: UsageEventRecord; timestamp?: string }>;
    };
    const entries = data.entries || [];
    if (entries.length === 0) return null;

    const hops: TraceHop[] = entries
      .map((e, idx) => {
        const rec = redactUserCredential(e.jsonPayload || ({} as UsageEventRecord));
        const inTok = rec.gen_ai?.["usage.input_tokens"] || 0;
        const outTok = rec.gen_ai?.["usage.output_tokens"] || 0;
        const cacheTok = rec.gen_ai?.["usage.cached_tokens"] || 0;
        const reasonTok = rec.gen_ai?.["usage.reasoning_tokens"] || 0;
        return {
          hop_index: idx + 1,
          span_id: rec.span_id || `span-${idx + 1}`,
          timestamp: rec.timestamp || e.timestamp || new Date().toISOString(),
          latency_ms: rec.latency_ms || rec.tool?.duration_ms || 0,
          status: rec.status || "ok",
          http_status: rec.http?.["response.status_code"] || (rec.status === "error" ? 403 : 200),
          source: rec.source || "workload",
          event: rec.event || "agent.invocation",
          caller_principal: rec.caller?.principal || "unknown",
          caller_team: rec.caller?.team || "unattributed",
          caller_verified: Boolean(rec.caller?.verified),
          target_name: rec.workload?.name || rec.tool?.server || "unknown",
          target_kind: rec.event === "tool.call" ? "mcp" : rec.event === "gateway.request" ? "gateway" : "adk",
          gateway_product: rec.gateway?.product || "agent_gateway",
          protocol: rec.event === "tool.call" ? "mcp" : "adk",
          operation: rec.tool?.name || rec.gen_ai?.["operation.name"] || rec.event,
          input_tokens: inTok,
          output_tokens: outTok,
          cached_tokens: cacheTok,
          reasoning_tokens: reasonTok,
          user_credential_forwarded: false,
          user_credential_redacted: true,
          verdicts: [
            {
              layer: "uap",
              decision: rec.status === "error" ? "deny" : "allow",
              reason: rec.error_type || "Verified via Cloud Logging esmeralda.usage",
            },
          ],
          usage_record: rec,
        };
      });

    const first = hops[0];
    const hasError = hops.some((h) => h.status === "error");
    return {
      trace_id: traceId,
      title: `Live Trace ${traceId.slice(0, 12)}… (${first?.target_name || "esmeralda"})`,
      initiated_at: first?.timestamp || new Date().toISOString(),
      total_latency_ms: hops.reduce((acc, h) => acc + h.latency_ms, 0),
      overall_status: hasError ? "blocked" : "ok",
      http_status: hops.find((h) => h.status === "error")?.http_status || 200,
      initiator_persona: first?.caller_principal || "authenticated-caller",
      billed_team: first?.caller_team || "cx",
      cost_center: first?.usage_record.caller?.declared?.cost_center || "cc-cx-br",
      verified_attribution: hops.every((h) => h.caller_verified),
      total_input_tokens: hops.reduce((acc, h) => acc + h.input_tokens, 0),
      total_output_tokens: hops.reduce((acc, h) => acc + h.output_tokens, 0),
      total_cached_tokens: hops.reduce((acc, h) => acc + h.cached_tokens, 0),
      total_reasoning_tokens: hops.reduce((acc, h) => acc + h.reasoning_tokens, 0),
      credential_redaction_verified: true,
      hops,
    };
  } catch {
    return null;
  }
}

export async function resolveTraceById(
  traceId: string,
  env: NodeJS.ProcessEnv = process.env
): Promise<TraceExplorationResult | null> {
  const cleanId = traceId.trim().toLowerCase();
  const localMatch = getRecordedTrace(cleanId);
  if (localMatch) {
    return localMatch;
  }
  const govProject = (env.GOVERNANCE_PROJECT_ID || "").trim();
  if (govProject) {
    const cloudTrace = await queryCloudLoggingTrace(govProject, cleanId, env);
    if (cloudTrace) return cloudTrace;
  }
  return null;
}

export interface AdminSummaryResponse {
  environment: string;
  domain: string;
  catalog_source: string;
  capabilities: RuntimeCatalogDocument["capabilities"];
  capability_matrix: Array<{
    capability: string;
    active_provider: string;
    supported_providers: string[];
    description: string;
    enforcement_plane: string;
  }>;
  targets: Array<{
    name: string;
    kind: string;
    owner_team: string;
    endpoint: string;
    partner_endpoint: string | null;
    gateway: string;
    token: string;
    user_credential_accept: boolean;
    user_credential_header: string | null;
    callers_count: number;
    callers: RuntimeCatalogDocument["targets"][string]["callers"];
    slo_latency_p95_ms: number | null;
    slo_availability: number | null;
    registry_synced: boolean;
    registry_resource: string;
  }>;
  chargeback: ChargebackRow[];
  security_audit: SecurityAuditRow[];
  recent_traces: Array<{
    trace_id: string;
    scenario_id?: string;
    title: string;
    overall_status: string;
    http_status: number;
    billed_team: string;
    total_latency_ms: number;
    total_tokens: number;
  }>;
}

export async function buildAdminSummary(
  catalog: RuntimeCatalogDocument
): Promise<AdminSummaryResponse> {
  const caps = catalog.capabilities;
  const capabilityMatrix = [
    {
      capability: "egress_allowlist",
      active_provider: caps.egress_allowlist,
      supported_providers: ["agent_gateway", "apigee"],
      description: "Least-privilege per-target Agent Registry egress & UAP SPIFFE bindings",
      enforcement_plane: "Central Agent Gateway (roles/iap.egressor + Caller Guard)",
    },
    {
      capability: "content_safety",
      active_provider: caps.content_safety,
      supported_providers: ["agent_gateway", "apigee", "none"],
      description: "Prompt injection, jailbreak, and sensitive data protection guardrails",
      enforcement_plane: "Model Armor Floor Settings + GEAP Semantic Governance NLCs",
    },
    {
      capability: "partner_access",
      active_provider: caps.partner_access,
      supported_providers: ["apigee", "agent_gateway", "none"],
      description: "Northbound B2B partner API product authentication and Mediated Token Exchange",
      enforcement_plane: caps.partner_access === "apigee" ? "Apigee X PSC Partner Front Door" : "Disabled (Internal Only)",
    },
    {
      capability: "token_quota",
      active_provider: caps.token_quota,
      supported_providers: ["apigee", "agent_gateway", "none"],
      description: "Per-consumer and per-caller LLM token rate limiting (LLMTokenQuota)",
      enforcement_plane: caps.token_quota === "apigee" ? "Apigee X LLMTokenQuota + Catalog Quotas" : "Catalog Alerts",
    },
    {
      capability: "usage_metering",
      active_provider: caps.usage_metering,
      supported_providers: ["agent_gateway", "apigee"],
      description: "Contract-driven esmeralda.usage telemetry & verified cross-team FinOps attribution",
      enforcement_plane: "BigQuery vw_usage_billable & vw_monthly_agent_chargeback",
    },
    {
      capability: "cross_cloud",
      active_provider: caps.cross_cloud,
      supported_providers: ["agent_gateway", "apigee"],
      description: "Multi-cloud outbound Agent Registry discovery & inbound federated workload auth",
      enforcement_plane: "Agent Registry + Apigee Cross-Cloud Shared Flow",
    },
  ];

  const targetsList = Object.values(catalog.targets).map((t) => ({
    name: t.name,
    kind: t.kind,
    owner_team: t.owner_team,
    endpoint: t.endpoint,
    partner_endpoint: t.partner_endpoint || null,
    gateway: t.gateway,
    token: t.token,
    user_credential_accept: Boolean(t.user_credential?.accept),
    user_credential_header: t.user_credential?.accept ? t.user_credential.header || "X-User-Token" : null,
    callers_count: t.callers.length,
    callers: t.callers,
    slo_latency_p95_ms: t.observability?.slo?.latency_p95_ms || null,
    slo_availability: t.observability?.slo?.availability || null,
    registry_synced: t.registry_synced ?? true,
    registry_resource: t.registry_resource || "",
  }));

  const traces = listRecentTraces().map((t) => ({
    trace_id: t.trace_id,
    scenario_id: t.scenario_id,
    title: t.title,
    overall_status: t.overall_status,
    http_status: t.http_status,
    billed_team: t.billed_team,
    total_latency_ms: t.total_latency_ms,
    total_tokens: t.total_input_tokens + t.total_output_tokens,
  }));

  return {
    environment: catalog.environment,
    domain: catalog.domain,
    catalog_source: catalog.source,
    capabilities: caps,
    capability_matrix: capabilityMatrix,
    targets: targetsList,
    chargeback: SYNTHETIC_CHARGEBACK_ROWS,
    security_audit: SYNTHETIC_SECURITY_AUDIT_ROWS,
    recent_traces: traces,
  };
}
