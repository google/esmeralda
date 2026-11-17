# Apigee Capability Bundles (`apps/apigee/`)

> Adapted in part from [`github.com/ricardosmh/adk-apigee-multiagent-demo`](https://github.com/ricardosmh/adk-apigee-multiagent-demo) under the Apache-2.0 License, re-authored for Esmeralda's capability-driven architecture and hardened for streaming agent traffic.

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

Each bundle under `apps/apigee/` backs one removable Terraform capability module in `infrastructure/modules/5-workloads/apigee/capabilities/`. When a capability is assigned to `agent_gateway` (the default) or disabled, its Apigee bundle and Terraform unit are excluded from deployment without touching agents, MCP servers, the service catalog, or internal DNS.

## Capability-to-Bundle Map

| `governance.capabilities` key | Bundle path | Terraform capability module | Purpose |
|---|---|---|---|
| `token_quota = "apigee"` | `sharedflows/token-quota/` | `capabilities/token-quota` | Enforces and increments per-consumer LLM token quotas (`LLMTokenQuota`) across **total tokens (`input + output`)** using API product attributes. |
| `usage_metering = "apigee"` | `sharedflows/usage-metering/` | `capabilities/usage-metering` | Extracts token usage from SSE `EventFlow` chunks (ADK `streamQuery`, Vertex AI Gemini, Anthropic) or non-streaming JSON responses, captures Apigee analytics data collectors, and emits a body-free OTel GenAI structured log entry in `PostClientFlow`. |
| `partner_access = "apigee"` (agents) | `proxies/partner-agents/` | `capabilities/partner-agents` | North-south partner proxy for catalog-allowlisted agents (such as `cx-mortgage-orchestrator`), separating SSE (`targets/sse.xml`) and non-SSE (`targets/default.xml`) target endpoints. |
| `partner_access = "apigee"` (MCP) | `proxies/partner-mcp/` | `capabilities/partner-mcp` | North-south partner proxy for catalog-allowlisted MCP servers via the internal entrypoint with server-side JSON-RPC `tools/call` scope enforcement. |
| `model_gateway = "apigee"` | `proxies/llm-gateway/` | `capabilities/llm-gateway` | Agent → model egress (base path `/llm`): verifies the agent's ID token (`VerifyJWT`: its unbound Agent Identity token against the trust domain's issuer, or a service account's Google ID token), allows registered callers, projects, locations and models only, enforces `LLMTokenQuota` per verified caller, and forwards Vertex AI Gemini `generateContent` / `streamGenerateContent` (SSE) with the proxy's own `GoogleAccessToken`. Agents opt in through `ESMERALDA_MODEL_GATEWAY_URL` (`esmeralda.models`). |

## Hardening & Streaming Fixes Applied

Compared to baseline Apigee proxy configurations, these bundles enforce the following production safeguards for agent and MCP traffic:

1. **Separated SSE and Non-SSE Target Endpoints (`targets/sse.xml` vs `targets/default.xml`)**: Mixing `text/event-stream` and `application/json` responses on a single `TargetEndpoint` with response body extraction can cause Apigee to buffer or empty streamed responses. `partner-agents` routes `:streamQuery` and `/v1/message:stream` to a dedicated `sse` target endpoint with `response.streaming.enabled = true` and `<EventFlow content-type="text/event-stream">`, while `:query`, `/v1/message:send`, and `/v1/card` route to `default`.
2. **Non-Buffered SSE Token Extraction & Guarded JSON Fallback**: `JS-extract-tokens` runs inside `<EventFlow content-type="text/event-stream">` on individual SSE frames (`response.event.current.content`). The JSON `ExtractVariables` fallback (`EV-ExtractTokenCount`) is strictly guarded by `!(response.header.Content-Type =| "text/event-stream")` so SSE streams are never buffered in memory.
3. **Three-Dialect Token Extraction (`extract-tokens.js`)**: Parses token metadata across:
   - **ADK `:streamQuery` and A2A `message/stream` SSE events** (`usage_metadata.prompt_token_count`, `usage_metadata.candidates_token_count`, `usage_metadata.total_token_count` at top-level or nested under `result.status.message` / `result.metadata`),
   - **Vertex AI Gemini `usageMetadata`** (`promptTokenCount`, `candidatesTokenCount`, `totalTokenCount`),
   - **Anthropic `message_start` / `message_delta`** (`input_tokens`, `output_tokens`).
4. **Total Token Quota (`input + output`) and Explicit `<LLMModelSource>`**: `LTQ-count.xml` charges `{llm.totalTokenCount}` (prompt + candidates tokens) rather than output tokens only, preventing large-prompt abuse from bypassing quotas. Both `LTQ-enforce.xml` and `LTQ-count.xml` set `<LLMModelSource>` explicitly so Apigee does not default to `jsonPath($.model)` on request bodies that omit a top-level `model` field.
5. **300-Second Streaming Timeouts**: Agent and MCP target endpoints set `io.timeout.millis = 300000` and `api.timeout = 300000` to accommodate multi-step reasoning and tool execution turns without premature gateway disconnects.
6. **Mandatory Consumer Authentication & Key-to-Identity Binding**: Every proxy executes `VAK-Verify-Partner-Key` (`VerifyAPIKey` on `x-api-key`) in `PreFlow`—no proxy allows unauthenticated pass-through. When an API product or developer app specifies an `owner_identity` attribute, `RF-Key-Identity-Mismatch` verifies that it matches the caller's verified identity claim.
7. **Server-Side Agent Allowlist & MCP Tool Scoping**:
   - `partner-agents` enforces that the target agent slug is in the product/KVM allowlist (`RF-Agent-Not-Allowed`), exposing only partner-enabled orchestrators (e.g., `cx-mortgage-orchestrator`) and blocking direct partner calls to internal specialist agents (`ai-coe-mortgage-specialist`).
   - `partner-mcp` executes `JS-Enforce-Tool-Scope` on JSON-RPC `tools/call` payloads to verify `params.name` against the API product's `allowed_mcp_tools` attribute (`RF-Tool-Forbidden`), enforcing least-privilege tool scoping at the gateway rather than relying on client-side filters.
8. **Caller Identity Preservation (`X-Forwarded-Authorization`)**: Because upstream `<Authentication>` (`GoogleAccessToken` / `GoogleIDToken`) overwrites the `Authorization` header, `AM-set-user-var` snapshots verified consumer metadata into flow variables and copies any incoming `Authorization` header into `X-Forwarded-Authorization` before `AM-clean-headers` strips `x-api-key`.
9. **Strict Southbound TLS Verification**: Every `HTTPTargetConnection` enables TLS with certificate validation (`<SSLInfo><Enabled>true</Enabled><Enforce>true</Enforce><IgnoreValidationErrors>false</IgnoreValidationErrors></SSLInfo>`).
10. **Body-Free `esmeralda.usage` Records (`ML-interaction`)**: Attached in `PostClientFlow`, `ML-interaction` emits one record per request that validates against the usage event contract (`contracts/usage-event.schema.json`): `schema`, `schema_version`, `event` (`gateway.request`, or `llm.call` for `llm-gateway`), `source: "gateway"`, `caller.principal` (verified app, cross-cloud `iss#sub` or agent identity; never the API key), `gen_ai.usage.*`, `trace_id` from `traceparent`, `latency_ms`, and an empty `dimensions`. `JS-Usage-Context` (`usage-context.js`, identical in every bundle) computes the values a message template can't (timestamp, latency, outcome, optional fragments) and also runs in a `DefaultFaultRule`, so rejected requests are recorded. It never logs `request.content` or `response.content`.

## Bundle Revisions

Each capability module zips its bundle with `data "archive_file"` and uploads a new proxy or shared flow revision whenever the zip's MD5 changes (`detect_md5hash`). The zip records file modes, so every `archive_file` pins `output_file_mode = "0644"`: the MD5, and therefore the revision, changes only when the bundle content changes, whichever checkout (clone, worktree, CI runner, umask) runs the plan. `tests/guards/test_apigee_bundle_drift_guards.py` enforces this.

A new revision of a proxy replaces its `google_apigee_environment_api_revision_deployment` (`-/+`), which briefly undeploys the proxy while the new revision deploys. Plan bundle changes for a maintenance window in production.

## Offline Validation

Run the offline policy and JavaScript conformance test suite from the repository root:

```bash
uv run pytest tests/apigee
```
