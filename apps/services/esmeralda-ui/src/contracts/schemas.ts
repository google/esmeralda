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

/**
 * TypeScript contract types and runtime validators for Esmeralda's three versioned contracts:
 * 1. `esmeralda.catalog` (`contracts/catalog.schema.json`)
 * 2. `esmeralda.runtime_catalog` (Governance Secret Manager `esmeralda-runtime-catalog-<env>`)
 * 3. `esmeralda.usage` (`contracts/usage-event.schema.json`)
 *
 * Zero hardcoded agent, tool, or gateway names: any target registered in `catalog/` renders
 * and dispatches automatically based on its declared `kind` (`adk`, `a2a`, `mcp`, `model`, `http`).
 */

export type TargetKind = "mcp" | "a2a" | "adk" | "model" | "http";
export type GatewayProduct = "direct" | "envoy" | "agent_gateway" | "apigee";
export type CapabilityProvider = "none" | "agent_gateway" | "apigee" | "envoy";
export type ServingTokenKind = "id" | "access";
export type ServingProbeKind = "mcp" | "a2a" | "query" | "none";

export interface GovernanceCapabilities {
  egress_allowlist: CapabilityProvider;
  content_safety: CapabilityProvider;
  partner_access: CapabilityProvider;
  token_quota: CapabilityProvider;
  usage_metering: CapabilityProvider;
  cross_cloud: CapabilityProvider;
}

export interface TargetUserCredentialRule {
  accept: boolean;
  header?: string;
  max_ttl_seconds?: number;
}

export interface TargetSlo {
  latency_p95_ms?: number;
  availability?: number;
}

export interface TargetObservability {
  usage_sources?: Array<"gateway" | "workload">;
  framework?: string;
  content_capture?: "none" | "metadata" | "redacted" | "full";
  sampling?: number;
  dimensions?: string[];
  slo?: TargetSlo;
}

export interface TargetCallerGrant {
  caller: string;
  team?: string;
  principal?: string;
  cost_centers?: string[];
  tools?: string[];
  gateway?: GatewayProduct;
  quota?: {
    tokens_per_minute?: number;
    requests_per_minute?: number;
  };
}

export interface RuntimeCatalogTarget {
  name: string;
  kind: TargetKind;
  description?: string;
  owner_team: string;
  unit?: string;
  managed: boolean;
  endpoint: string;
  partner_endpoint?: string;
  token: ServingTokenKind;
  probe: ServingProbeKind;
  timeout?: number;
  gateway: GatewayProduct;
  user_credential: TargetUserCredentialRule;
  models?: string[];
  observability?: TargetObservability;
  callers: TargetCallerGrant[];
  registry_synced?: boolean;
  registry_resource?: string;
}

export interface CatalogDimensionSpec {
  description?: string;
  values: string[];
}

export interface CatalogTeamSpec {
  description?: string;
  cost_centers: string[];
  country?: string;
  external?: boolean;
}

export interface PrincipalTeamRow {
  caller: string;
  principal: string;
  principal_type: string;
  team: string;
  cost_centers: string[];
}

export interface RuntimeCatalogDocument {
  schema: "esmeralda.runtime_catalog" | "esmeralda.catalog";
  schema_version: 1;
  environment: string;
  domain: string;
  generated_at?: string;
  source: "secret_manager" | "resolved_file" | "synthetic_fixture";
  capabilities: GovernanceCapabilities;
  attribution_policy: {
    unmapped_caller: "deny" | "flag";
    declared_mismatch: "reject" | "bill_own_team";
  };
  teams: Record<string, CatalogTeamSpec>;
  dimensions: Record<string, CatalogDimensionSpec>;
  targets: Record<string, RuntimeCatalogTarget>;
  principal_teams: PrincipalTeamRow[];
}

// ---------------------------------------------------------------------------
// Usage Event Contract (`contracts/usage-event.schema.json`)
// ---------------------------------------------------------------------------

export type UsageEventType = "llm.call" | "tool.call" | "agent.invocation" | "gateway.request";
export type UsageSource = "gateway" | "workload";
export type WorkloadRuntime = "agent_engine" | "cloud_run" | "gke" | "external";
export type CloudProvider = "gcp" | "aws" | "azure" | "on_prem" | "other";

export interface UsageWorkload {
  name: string;
  team?: string;
  runtime?: WorkloadRuntime;
  framework?: string;
  cloud?: CloudProvider;
}

export interface UsageCaller {
  principal?: string;
  team?: string;
  verified: boolean;
  declared?: Record<string, string>;
}

export interface UsageGenAi {
  system?: string;
  "operation.name"?: string;
  "request.model"?: string;
  "response.model"?: string;
  "response.finish_reasons"?: string[];
  "usage.input_tokens"?: number;
  "usage.output_tokens"?: number;
  "usage.cached_tokens"?: number;
  "usage.reasoning_tokens"?: number;
  "agent.name"?: string;
  "conversation.id"?: string;
}

export interface UsageTool {
  name: string;
  server?: string;
  call_id?: string;
  status: "ok" | "error";
  error_type?: string;
  duration_ms?: number;
}

export interface UsageGateway {
  product: "apigee" | "envoy" | "agent_gateway" | "direct" | "other";
  name?: string;
}

export interface UsageHttp {
  "request.method"?: string;
  "response.status_code"?: number;
  route?: string;
}

export interface UsageEventRecord {
  schema: "esmeralda.usage";
  schema_version: 1;
  event: UsageEventType;
  timestamp: string;
  trace_id?: string;
  span_id?: string;
  source: UsageSource;
  workload?: UsageWorkload;
  caller?: UsageCaller;
  gen_ai?: UsageGenAi;
  tool?: UsageTool;
  gateway?: UsageGateway;
  http?: UsageHttp;
  status?: "ok" | "error";
  error_type?: string;
  dimensions?: Record<string, string>;
  latency_ms?: number;
}

// ---------------------------------------------------------------------------
// Governance Verdicts & Multi-Hop Trace Waterfall
// ---------------------------------------------------------------------------

export interface GovernanceVerdict {
  layer: "iap" | "caller_guard" | "uap" | "semantic_nlc" | "model_armor" | "token_quota";
  decision: "allow" | "deny" | "flag" | "inspect";
  policy_id?: string;
  reason: string;
  detail?: string;
}

export interface TraceHop {
  hop_index: number;
  span_id: string;
  parent_span_id?: string;
  timestamp: string;
  latency_ms: number;
  status: "ok" | "error";
  http_status?: number;
  source: UsageSource;
  event: UsageEventType;
  caller_principal: string;
  caller_team: string;
  caller_verified: boolean;
  target_name: string;
  target_kind: TargetKind | "gateway" | "llm";
  gateway_product?: GatewayProduct | "other";
  protocol: "adk" | "a2a" | "mcp" | "vertex_gemini" | "http";
  operation: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  user_credential_forwarded: boolean;
  user_credential_redacted: boolean;
  verdicts: GovernanceVerdict[];
  usage_record: UsageEventRecord;
}

export interface TraceExplorationResult {
  trace_id: string;
  scenario_id?: string;
  title: string;
  initiated_at: string;
  total_latency_ms: number;
  overall_status: "ok" | "blocked";
  blocked_by?: string;
  http_status: number;
  initiator_persona: string;
  billed_team: string;
  cost_center: string;
  verified_attribution: boolean;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cached_tokens: number;
  total_reasoning_tokens: number;
  credential_redaction_verified: boolean;
  hops: TraceHop[];
}

// ---------------------------------------------------------------------------
// Validators & Redaction Guards
// ---------------------------------------------------------------------------

const VALID_EVENTS = new Set<string>(["llm.call", "tool.call", "agent.invocation", "gateway.request"]);
const VALID_SOURCES = new Set<string>(["gateway", "workload"]);
const VALID_KINDS = new Set<string>(["mcp", "a2a", "adk", "model", "http"]);
const TRACE_ID_RE = /^[0-9a-f]{32}$/;
const SPAN_ID_RE = /^[0-9a-f]{16}$/;
const NAME_RE = /^[a-z][a-z0-9-]{0,62}$/;
const DIMENSION_KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;

const SECRET_KEY_RE =
  /(passw|secret|credential|authori[sz]ation|api[-_.]?key|cookie|private[-_.]?key|bearer|(^|[-_.])token([-_.]|$)|(access|refresh|id|auth|oauth|user|session|delegated)[-_.]?token|x-esmeralda-user-assertion|x-user-token)/i;

const SECRET_VALUE_RE =
  /(bearer\s+[\w.~+/=-]+|\bya29\.[\w.-]+|\beyJ[\w-]+\.[\w-]+\.[\w-]*|\bgh[pousr]_\w{20,}|\bAIza[\w-]{30,})/gi;

export const REDACTED = "[REDACTED]";

export function isSecretKey(key: string): boolean {
  return SECRET_KEY_RE.test(key);
}

/**
 * Redacts delegated user credentials, bearer tokens, JWTs, OAuth access tokens,
 * and sensitive header/field keys recursively from any object or string before logging
 * or emitting `esmeralda.usage` records.
 */
export function redactUserCredential<T>(value: T, explicitSecrets: Array<string | null | undefined> = []): T {
  const needles = explicitSecrets
    .filter((s): s is string => typeof s === "string" && s.trim().length > 0)
    .sort((a, b) => b.length - a.length);

  function scrubString(str: string): string {
    let out = str;
    for (const needle of needles) {
      if (out.includes(needle)) {
        out = out.split(needle).join(REDACTED);
      }
    }
    return out.replace(SECRET_VALUE_RE, REDACTED);
  }

  function walk(node: unknown): unknown {
    if (typeof node === "string") {
      return scrubString(node);
    }
    if (Array.isArray(node)) {
      return node.map((item) => walk(item));
    }
    if (node !== null && typeof node === "object") {
      const result: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
        if (isSecretKey(k)) {
          continue;
        } else {
          result[k] = walk(v);
        }
      }
      return result;
    }
    return node;
  }

  return walk(value) as T;
}

/**
 * Validates an `esmeralda.usage` record against `contracts/usage-event.schema.json`.
 * Returns an array of validation error strings (empty when valid).
 */
export function validateUsageEvent(record: unknown): string[] {
  const errors: string[] = [];
  if (record === null || typeof record !== "object" || Array.isArray(record)) {
    return ["<record>: must be a JSON object"];
  }
  const r = record as Record<string, unknown>;

  if (r.schema !== "esmeralda.usage") {
    errors.push(`schema: expected 'esmeralda.usage', got ${JSON.stringify(r.schema)}`);
  }
  if (r.schema_version !== 1) {
    errors.push(`schema_version: expected 1, got ${JSON.stringify(r.schema_version)}`);
  }
  if (typeof r.event !== "string" || !VALID_EVENTS.has(r.event)) {
    errors.push(`event: must be one of llm.call, tool.call, agent.invocation, gateway.request`);
  }
  if (typeof r.timestamp !== "string" || Number.isNaN(Date.parse(r.timestamp))) {
    errors.push("timestamp: must be a valid RFC 3339 timestamp string");
  }
  if (typeof r.source !== "string" || !VALID_SOURCES.has(r.source)) {
    errors.push("source: must be 'gateway' or 'workload'");
  }
  if (r.trace_id !== undefined && (typeof r.trace_id !== "string" || !TRACE_ID_RE.test(r.trace_id))) {
    errors.push("trace_id: must be 32 lowercase hex characters");
  }
  if (r.span_id !== undefined && (typeof r.span_id !== "string" || !SPAN_ID_RE.test(r.span_id))) {
    errors.push("span_id: must be 16 lowercase hex characters");
  }

  if (r.source === "workload") {
    if (!r.workload || typeof r.workload !== "object") {
      errors.push("workload: required when source is 'workload'");
    } else {
      const wl = r.workload as Record<string, unknown>;
      if (typeof wl.name !== "string" || wl.name.trim().length === 0) {
        errors.push("workload.name: required non-empty string");
      }
    }
  }

  if (r.event === "llm.call") {
    const ga = r.gen_ai as Record<string, unknown> | undefined;
    if (!ga || typeof ga !== "object") {
      errors.push("gen_ai: required when event is 'llm.call'");
    } else if (!ga["request.model"] && !ga["response.model"]) {
      errors.push("gen_ai: requires 'request.model' or 'response.model' for llm.call");
    }
  }

  if (r.event === "tool.call") {
    const tl = r.tool as Record<string, unknown> | undefined;
    if (!tl || typeof tl !== "object" || typeof tl.name !== "string" || !tl.status) {
      errors.push("tool: required with 'name' and 'status' when event is 'tool.call'");
    }
  }

  if (r.event === "agent.invocation") {
    const ga = r.gen_ai as Record<string, unknown> | undefined;
    if (!ga || typeof ga !== "object" || typeof ga["agent.name"] !== "string") {
      errors.push("gen_ai.agent.name: required when event is 'agent.invocation'");
    }
  }

  if (r.event === "gateway.request") {
    if (r.source !== "gateway") {
      errors.push("source: must be 'gateway' when event is 'gateway.request'");
    }
    const gw = r.gateway as Record<string, unknown> | undefined;
    if (!gw || typeof gw !== "object" || typeof gw.product !== "string") {
      errors.push("gateway.product: required when event is 'gateway.request'");
    }
  }

  if (r.dimensions !== undefined) {
    if (typeof r.dimensions !== "object" || r.dimensions === null || Array.isArray(r.dimensions)) {
      errors.push("dimensions: must be a string map");
    } else {
      for (const [k, v] of Object.entries(r.dimensions as Record<string, unknown>)) {
        if (!DIMENSION_KEY_RE.test(k) || typeof v !== "string") {
          errors.push(`dimensions.${k}: invalid dimension key or value`);
        }
      }
    }
  }

  return errors;
}

/**
 * Validates a `RuntimeCatalogDocument` loaded from Governance Secret Manager or resolved catalog JSON.
 */
export function validateRuntimeCatalog(doc: unknown): string[] {
  const errors: string[] = [];
  if (doc === null || typeof doc !== "object" || Array.isArray(doc)) {
    return ["<catalog>: must be a JSON object"];
  }
  const d = doc as Record<string, unknown>;
  if (d.schema !== "esmeralda.runtime_catalog" && d.schema !== "esmeralda.catalog") {
    errors.push(`schema: expected 'esmeralda.runtime_catalog' or 'esmeralda.catalog', got ${JSON.stringify(d.schema)}`);
  }
  if (d.schema_version !== 1) {
    errors.push(`schema_version: expected 1, got ${JSON.stringify(d.schema_version)}`);
  }
  if (typeof d.environment !== "string" || !NAME_RE.test(d.environment)) {
    errors.push("environment: must be a valid DNS-label name");
  }
  if (!d.targets || typeof d.targets !== "object" || Array.isArray(d.targets)) {
    errors.push("targets: must be a map of catalog targets");
  } else {
    for (const [targetName, rawTarget] of Object.entries(d.targets as Record<string, unknown>)) {
      if (!NAME_RE.test(targetName)) {
        errors.push(`targets.${targetName}: invalid target name`);
      }
      if (!rawTarget || typeof rawTarget !== "object") {
        errors.push(`targets.${targetName}: must be an object`);
        continue;
      }
      const t = rawTarget as Record<string, unknown>;
      if (typeof t.kind !== "string" || !VALID_KINDS.has(t.kind)) {
        errors.push(`targets.${targetName}.kind: invalid kind ${JSON.stringify(t.kind)}`);
      }
      if (typeof t.owner_team !== "string" || !NAME_RE.test(t.owner_team)) {
        errors.push(`targets.${targetName}.owner_team: invalid owner_team`);
      }
    }
  }
  return errors;
}
