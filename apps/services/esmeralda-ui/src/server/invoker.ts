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

import crypto from "node:crypto";
import {
  redactUserCredential,
  validateUsageEvent,
  type RuntimeCatalogDocument,
  type RuntimeCatalogTarget,
  type TraceExplorationResult,
  type UsageEventRecord,
} from "../contracts/schemas.ts";
import { POLICY_VERIFICATION_SCENARIOS, SYNTHETIC_TRACES } from "./fixtures.ts";
import { fetchAccessToken, fetchIdToken } from "./gcpAuth.ts";
import type { VerifiedIdentity } from "./identity.ts";

/**
 * Protocol-Driven Invocation Dispatcher (`/api/invoke`).
 *
 * Dispatches dynamically based on the catalog target's `kind` (`adk`, `a2a`, `mcp`)
 * and ingress path (`direct` vs. `partner`):
 * - Propagates W3C `traceparent` (`00-<trace_id>-<span_id>-01`).
 * - Attaches the delegated user credential (`user_credential.header`, e.g. `X-User-Token` or
 *   `X-Esmeralda-User-Assertion`) ONLY when the target's catalog entry has `user_credential.accept === true`.
 * - Redacts all user credentials via `redactUserCredential` before emitting a structured
 *   `esmeralda.usage` JSON log line on stdout.
 */

export interface InvokeRequestPayload {
  target: string;
  ingress?: "direct" | "partner";
  message: string;
  persona_id?: string;
  cost_center?: string;
  dimensions?: Record<string, string>;
  forward_user_credential?: boolean;
  user_credential_value?: string;
  scenario_id?: string;
  tool_name?: string;
  tool_arguments?: Record<string, unknown>;
}

export interface InvokeResponsePayload {
  ok: boolean;
  http_status: number;
  trace_id: string;
  span_id: string;
  traceparent: string;
  target: string;
  target_kind: string;
  ingress: "direct" | "partner";
  endpoint_url: string;
  protocol_payload: Record<string, unknown>;
  user_credential_forwarded: boolean;
  user_credential_header: string | null;
  user_credential_redacted: boolean;
  caller: {
    email: string;
    team: string;
    cost_center: string;
    verified: boolean;
  };
  reply: string;
  usage_event: UsageEventRecord;
  trace_preview: TraceExplorationResult;
}

const recentTraces = new Map<string, TraceExplorationResult>();

export function getRecordedTrace(traceId: string): TraceExplorationResult | undefined {
  return recentTraces.get(traceId) || SYNTHETIC_TRACES[traceId];
}

export function listRecentTraces(): TraceExplorationResult[] {
  const combined = new Map<string, TraceExplorationResult>();
  for (const [id, t] of recentTraces.entries()) {
    combined.set(id, t);
  }
  for (const [id, t] of Object.entries(SYNTHETIC_TRACES)) {
    if (!combined.has(id)) {
      combined.set(id, t);
    }
  }
  return Array.from(combined.values());
}

/**
 * Builds the wire-protocol request body for any target `kind` (`adk`, `a2a`, `mcp`).
 * Zero hardcoded agent or tool names!
 */
export function buildProtocolPayload(
  target: RuntimeCatalogTarget,
  req: InvokeRequestPayload,
  identity: VerifiedIdentity
): { pathSuffix: string; body: Record<string, unknown> } {
  const callerContext = {
    project_id: `esm-${identity.persona.team}`,
    agent_name: "esmeralda-ui",
    cost_center: req.cost_center || identity.persona.cost_center,
    country: identity.persona.country,
  };

  if (target.kind === "adk") {
    return {
      pathSuffix: "/api/stream_reasoning_engine",
      body: {
        class_method: "async_stream_query",
        input: {
          message: req.message,
          user_id: identity.email,
          state_delta: {
            "temp:caller_context": callerContext,
          },
        },
      },
    };
  }

  if (target.kind === "a2a") {
    return {
      pathSuffix: "/a2a",
      body: {
        jsonrpc: "2.0",
        id: `a2a-${crypto.randomUUID()}`,
        method: "message/send",
        params: {
          message: {
            role: "user",
            messageId: `msg-${crypto.randomUUID()}`,
            parts: [{ kind: "text", text: req.message }],
          },
          metadata: {
            caller_context: callerContext,
          },
        },
      },
    };
  }

  // `mcp` JSON-RPC `tools/call`
  const inferredTool =
    req.tool_name ||
    target.callers.find((c) => c.tools && c.tools.length > 0)?.tools?.[0] ||
    "invoke";
  return {
    pathSuffix: "",
    body: {
      jsonrpc: "2.0",
      id: `mcp-${crypto.randomUUID()}`,
      method: "tools/call",
      params: {
        name: inferredTool,
        arguments: req.tool_arguments || { query: req.message },
      },
    },
  };
}

export async function invokeCatalogTarget(
  catalog: RuntimeCatalogDocument,
  req: InvokeRequestPayload,
  identity: VerifiedIdentity,
  env: NodeJS.ProcessEnv = process.env,
  emitStdout = true
): Promise<InvokeResponsePayload> {
  const target = catalog.targets[req.target];
  if (!target) {
    throw new Error(`Unknown catalog target '${req.target}'. Available targets: ${Object.keys(catalog.targets).join(", ")}`);
  }

  const ingress: "direct" | "partner" = req.ingress === "partner" ? "partner" : "direct";
  const baseUrl =
    ingress === "partner" && target.partner_endpoint
      ? target.partner_endpoint
      : target.endpoint;

  const { pathSuffix, body: rawProtocolBody } = buildProtocolPayload(target, req, identity);
  const endpointUrl = baseUrl.endsWith(pathSuffix) ? baseUrl : `${baseUrl.replace(/\/$/, "")}${pathSuffix}`;

  // Determine whether a scripted verification scenario matches
  const matchedScenario =
    (req.scenario_id ? POLICY_VERIFICATION_SCENARIOS.find((s) => s.id === req.scenario_id) : undefined) ||
    POLICY_VERIFICATION_SCENARIOS.find(
      (s) =>
        s.target === req.target &&
        (s.persona_id === identity.persona.id || s.prompt.toLowerCase() === req.message.trim().toLowerCase())
    );

  // Check if persona is unauthorized (`contractor-marketing` or unmapped cost center)
  const isUnauthorizedPersona =
    identity.persona.team === "unmapped-marketing" ||
    (req.cost_center && !["cc-cx-br", "cc-cx-mx", "cc-ai-coe", "cc-platform"].includes(req.cost_center));

  const traceId =
    matchedScenario?.expected_trace_id || crypto.randomBytes(16).toString("hex");
  const spanId = crypto.randomBytes(8).toString("hex");
  const traceparent = `00-${traceId}-${spanId}-01`;

  // Opt-in delegated user credential rule (`user_credential.accept === true` in catalog)
  const rawCredential = req.user_credential_value || identity.persona.synthetic_assertion;
  const shouldForwardCredential = Boolean(
    req.forward_user_credential !== false && target.user_credential?.accept === true && rawCredential
  );
  const credentialHeader = shouldForwardCredential
    ? target.user_credential.header || "X-Esmeralda-User-Assertion"
    : null;

  const outgoingHeaders: Record<string, string> = {
    "content-type": "application/json",
    traceparent,
    "x-esmeralda-cost-center": req.cost_center || identity.persona.cost_center,
  };
  if (credentialHeader && shouldForwardCredential) {
    outgoingHeaders[credentialHeader] = rawCredential;
  }

  // Mint Google ID or Access token when live upstream mode is enabled
  let liveResponseText: string | null = null;
  let liveHttpStatus: number | null = null;
  const liveDispatchEnabled = env.ESMERALDA_UI_LIVE_DISPATCH === "true";

  if (liveDispatchEnabled) {
    const token =
      target.token === "id"
        ? await fetchIdToken(new URL(endpointUrl).origin, env)
        : await fetchAccessToken(env);
    if (token) {
      outgoingHeaders.authorization = `Bearer ${token}`;
    }
    try {
      const res = await fetch(endpointUrl, {
        method: "POST",
        headers: outgoingHeaders,
        body: JSON.stringify(rawProtocolBody),
        signal: AbortSignal.timeout((target.timeout || 30) * 1000),
      });
      liveHttpStatus = res.status;
      liveResponseText = await res.text();
    } catch (err) {
      liveHttpStatus = 502;
      liveResponseText = `Upstream connection error: ${(err as Error).message}`;
    }
  }

  // Determine final status and reply
  let httpStatus = 200;
  let reply = "";
  let traceResult: TraceExplorationResult;

  if (liveHttpStatus !== null && liveResponseText !== null) {
    httpStatus = liveHttpStatus;
    reply = redactUserCredential(liveResponseText, [rawCredential]);
    traceResult = SYNTHETIC_TRACES["4bf92f3577b34da6a3ce929d0e0e4736"];
  } else if (matchedScenario && SYNTHETIC_TRACES[matchedScenario.expected_trace_id]) {
    httpStatus = matchedScenario.expected_http_status;
    reply = matchedScenario.summary_response;
    traceResult = SYNTHETIC_TRACES[matchedScenario.expected_trace_id];
  } else if (isUnauthorizedPersona) {
    const denyScenario = POLICY_VERIFICATION_SCENARIOS[1];
    httpStatus = 403;
    reply = denyScenario.summary_response;
    traceResult = {
      ...SYNTHETIC_TRACES[denyScenario.expected_trace_id],
      trace_id: traceId,
    };
  } else {
    const happyScenario = POLICY_VERIFICATION_SCENARIOS[0];
    httpStatus = 200;
    reply =
      `[${target.name} (${target.kind.toUpperCase()}) via ${ingress}]\n\n` +
      happyScenario.summary_response;
    traceResult = {
      ...SYNTHETIC_TRACES[happyScenario.expected_trace_id],
      trace_id: traceId,
      initiator_persona: identity.email,
      cost_center: req.cost_center || identity.persona.cost_center,
    };
  }

  recentTraces.set(traceId, traceResult);

  // Build and redact the `esmeralda.usage` event record
  const rawUsageRecord: UsageEventRecord = {
    schema: "esmeralda.usage",
    schema_version: 1,
    event: target.kind === "mcp" ? "tool.call" : "agent.invocation",
    timestamp: new Date().toISOString(),
    trace_id: traceId,
    span_id: spanId,
    source: "workload",
    workload: {
      name: target.name,
      team: target.owner_team,
      runtime: target.kind === "mcp" ? "cloud_run" : "agent_engine",
      framework: target.observability?.framework || (target.kind === "mcp" ? "custom" : "adk"),
      cloud: "gcp",
    },
    caller: {
      principal: identity.subject,
      team: isUnauthorizedPersona ? undefined : identity.persona.team,
      verified: !isUnauthorizedPersona,
      declared: {
        cost_center: req.cost_center || identity.persona.cost_center,
        ingress_mode: ingress,
      },
    },
    ...(target.kind === "mcp"
      ? {
          tool: {
            name: String((rawProtocolBody.params as Record<string, unknown>)?.name || "invoke"),
            server: target.name,
            status: httpStatus < 400 ? "ok" : "error",
            ...(httpStatus >= 400 ? { error_type: `HTTP_${httpStatus}` } : {}),
            duration_ms: traceResult.total_latency_ms,
          },
        }
      : {
          gen_ai: {
            system: "gcp.vertex_ai",
            "operation.name": "invoke_agent",
            "agent.name": target.name,
            "request.model": target.models?.[0] || "gemini-3.7-flash",
            "usage.input_tokens": traceResult.total_input_tokens,
            "usage.output_tokens": traceResult.total_output_tokens,
            "usage.cached_tokens": traceResult.total_cached_tokens,
            "usage.reasoning_tokens": traceResult.total_reasoning_tokens,
          },
        }),
    http: {
      "request.method": "POST",
      "response.status_code": httpStatus,
      route: endpointUrl,
    },
    status: httpStatus < 400 ? "ok" : "error",
    ...(httpStatus >= 400 ? { error_type: traceResult.blocked_by || `HTTP_${httpStatus}` } : {}),
    ...(req.dimensions && Object.keys(req.dimensions).length > 0 ? { dimensions: req.dimensions } : {}),
    latency_ms: traceResult.total_latency_ms,
  };

  const redactedUsageRecord = redactUserCredential(rawUsageRecord, [rawCredential]);
  const validationErrors = validateUsageEvent(redactedUsageRecord);
  if (validationErrors.length > 0) {
    throw new Error(`Generated usage event violated contracts/usage-event.schema.json: ${validationErrors.join("; ")}`);
  }

  if (emitStdout && env.NODE_ENV !== "test") {
    process.stdout.write(JSON.stringify(redactedUsageRecord) + "\n");
  }

  const redactedProtocolPayload = redactUserCredential(
    {
      endpoint: endpointUrl,
      headers: outgoingHeaders,
      body: rawProtocolBody,
    },
    [rawCredential]
  );

  return {
    ok: httpStatus >= 200 && httpStatus < 300,
    http_status: httpStatus,
    trace_id: traceId,
    span_id: spanId,
    traceparent,
    target: target.name,
    target_kind: target.kind,
    ingress,
    endpoint_url: endpointUrl,
    protocol_payload: redactedProtocolPayload,
    user_credential_forwarded: shouldForwardCredential,
    user_credential_header: credentialHeader,
    user_credential_redacted: true,
    caller: {
      email: identity.email,
      team: identity.persona.team,
      cost_center: req.cost_center || identity.persona.cost_center,
      verified: !isUnauthorizedPersona,
    },
    reply: redactUserCredential(reply, [rawCredential]),
    usage_event: redactedUsageRecord,
    trace_preview: traceResult,
  };
}
