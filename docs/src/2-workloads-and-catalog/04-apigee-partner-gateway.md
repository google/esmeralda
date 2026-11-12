---
title: "Apigee as an LLM and MCP gateway: token quotas and partner monetization"
description: "Expose agents and MCP tools to partners through Apigee X: API products, LLMTokenQuota enforcement on streaming responses, usage metering and tiered plans."
---

# 1.4 Partner API Management & Token Monetization with Apigee X

In **[1.3 The Private Front Door & Pluggable Gateways](./01-ingress-gateways.md)**, we saw how internal workloads inside the bank's VPC call `*.esmeralda.internal` through the internal HTTPS load balancer (`entrypoint`), using the built-in **`direct`** backend by default. And in **[2.1 Central Agent Gateway & Private PKI](../3-agentops-and-lifecycle/01-central-agent-gateway.md)**, we will see how every internal AI agent's outbound traffic is governed by Google's **Central Agent Gateway** using attested **SPIFFE Agent Identities (`principal://`)**.

But what happens when the business wants to expose Julian Sterling's mortgage underwriting workflow to **external B2B partners**—such as third-party real-estate portals, partner brokerage mobile apps, or external fintech aggregators?

External partners sit outside our Google Cloud organization:
1. **They do not have internal SPIFFE identities or GCP IAM service accounts.** They authenticate with commercial **API keys (`x-api-key`)** or **OAuth 2.0 client credentials** provisioned through a developer portal.
2. **They must never be allowed to call internal specialist agents or raw MCP tools directly.** A partner app may call the front-door `cx-mortgage-orchestrator`, or a curated read-only MCP tool (`verify_applicant`), but must receive an immediate `HTTP 403` if it tries to invoke `ai-coe-mortgage-specialist` directly or call administrative MCP tools.
3. **They require contractual LLM token quotas and billing attribution.** If a Bronze-tier partner is contracted for 10,000 Gemini tokens per minute, the gateway must parse Server-Sent Events (SSE) token usage in flight, deduct the exact prompt + candidate tokens consumed, and reject subsequent calls with `HTTP 429` once the budget is exhausted—*before* invoking Vertex AI.

This section explains **exactly how Apigee X works in Esmeralda** from network plumbing down to XML policies, JavaScript callouts, SSE token extraction, and how it hands off seamlessly to **GEAP Agent Identity** and the **Central Agent Gateway**.

---

## 1. Division of Responsibilities: Apigee X vs. GEAP Agent Identity vs. Central Agent Gateway

Before diving into the proxy bundles, it is important to see how **Apigee X**, **GEAP Agent Identity**, and the **Central Agent Gateway** divide responsibilities without overlapping:

| Layer / Component | Traffic Direction | Primary Role | What It Enforces |
| :--- | :--- | :--- | :--- |
| **1. Apigee X** (`partner.esmeralda.internal` / `10.0.1.3`) | **North–South (External / Partner Ingress)** | **Commercial & Partner API Management** | • Verifies partner API Keys / OAuth tokens (`VerifyAPIKey`) against tiered **API Products** (`bronze`, `silver`, `gold`)<br>• Enforces **agent & MCP tool exposure allowlists** (`kvm-partner-agents`, `kvm-partner-mcp`)<br>• Enforces per-partner **LLM Token Quotas** (`<LLMTokenQuota>` `EnforceOnly` + `CountOnly` → `HTTP 429`)<br>• Emits per-partner **Monetization & Token Analytics** (`DataCapture` + `PostClientFlow` `MessageLogging`) |
| **2. GEAP Agent Identity & Auth Manager** (`agents.global.org-<ORG>.system.id.goog`) | **End-to-End Cryptographic Identity** | **Workload & Delegated Human Identity** | • Assigns each Reasoning Engine a unique X.509 **SPIFFE ID (`principal://`)**<br>• Brokers **3-Legged OAuth (3LO)** end-user tokens (`X-Esmeralda-User-Token`) so downstream MCP tools can verify which human user authorized the call (`esmeralda.mcp_authz`) |
| **3. GEAP Central Agent Gateway** (`10.200.0.2`) | **East–West (Internal Agent Egress)** | **Zero-Trust Runtime Policy Enforcement** | • Enforces **Agent Registry** destination allowlists<br>• Enforces per-target **IAP `roles/iap.egressor`** policies on each Agent Registry entry: only `cx-mortgage-orchestrator` is listed on `ai-coe-mortgage-specialist`, and only `ai-coe-mortgage-specialist` on the MCP servers (A2A calls through the generic Vertex AI endpoint are a known gap, see [Security & IAM](../1-platform-foundations/03-security-iam-and-telemetry.md#4-three-identity-delegation-per-target-spiffe-governance))<br>• Evaluates **Semantic Governance (Natural Language Constraints, NLC)** and optional **Model Armor** on unary MCP tool calls |

---

## 2. End-to-End Architecture & Packet Flow

When an external partner app calls `https://partner.esmeralda.internal/partner/v1/agents/cx-mortgage-orchestrator:streamQuery`, the request traverses both the **North–South Apigee X front door** and the **East–West GEAP Agent Identity + Central Agent Gateway loop**:

```mermaid
sequenceDiagram
    autonumber
    participant Partner as External Partner App
    participant EP as Entrypoint HTTPS ALB<br/>(partner.esmeralda.internal / 10.0.1.3)
    participant Apigee as Apigee X Runtime<br/>(partner-agents proxy)
    participant Orch as cx-mortgage-orchestrator<br/>(SPIFFE Identity #1)
    participant AGW as Central Agent Gateway (10.200.0.2)<br/>(UAP/IAP + Semantic NLC)
    participant Spec as ai-coe-mortgage-specialist<br/>(SPIFFE Identity #2 + 3LO)
    participant MCP as Cloud Run MCP Servers<br/>(DMS, Income, Email)

    Partner->>EP: POST /partner/v1/agents/cx-mortgage-orchestrator:streamQuery<br/>(x-api-key + X-Esmeralda-User-Token)
    EP->>Apigee: Route via PSC NEG (neg-apigee-psc-<env>)
    Note over Apigee: 1. VAK-Partner-Key (401 if invalid)<br/>2. KVM-Get-Agent-Config + JS-Validate-Agent (403 if not orchestrator)<br/>3. FC-Token-Quota-Enforce (429 if token quota exhausted)<br/>4. JS-Rewrite-Target-Path + mint GoogleAccessToken (sa-esmeralda-apigee-proxy)
    Apigee->>Orch: POST /v1beta1/.../reasoningEngines/<id>:streamQuery?alt=sse
    Note over Orch: Attests SPIFFE Identity #1 (principal://...)
    Orch->>AGW: A2A Task to ai-coe-mortgage-specialist.esmeralda.internal
    Note over AGW: Verify SPIFFE Identity #1 holds roles/iap.egressor on Specialist
    AGW->>Spec: Route via Entrypoint direct PSC NEG
    Note over Spec: Attests SPIFFE Identity #2 + Auth Manager 3LO user claims
    Spec->>AGW: MCP tool calls (verify_applicant, search_documents)
    Note over AGW: Verify SPIFFE Identity #2 + Semantic Governance (NLC)
    AGW->>MCP: Forward with verified identity + 3LO user claims
    MCP-->>Spec: Verified financial records ($185,000/yr)
    Spec-->>Orch: Underwriting assessment
    Orch-->>Apigee: SSE Stream chunks + usageMetadata token counts
    Note over Apigee: 5. EventFlow + extract-tokens.js parses prompt/candidate/total tokens<br/>6. LTQ-count deducts tokens from partner product quota<br/>7. PostClientFlow logs ML-interaction JSON to Cloud Logging + Analytics
    Apigee-->>EP: Governed SSE Stream
    EP-->>Partner: HTTP 200 text/event-stream (or HTTP 429 on next turn if quota exceeded)
```

---

## 3. Private Networking & PSC Bridge (`apigee/platform` & `entrypoint`)

How does traffic get from our Shared VPC into Apigee X's Google-managed tenant project—and from Apigee X back to our private `*.esmeralda.internal` services—with zero public IP addresses?

```mermaid
flowchart LR
    subgraph SharedVPC["Shared VPC (esm-<env>-net-host-<sfx>)"]
        DNS["Cloud DNS Private Zone<br/>partner.esmeralda.internal -> 10.0.1.3"]
        ALB["Entrypoint Internal HTTPS ALB<br/>(ilb-esmeralda-partner-rule-<env>)"]
        PSCNEG["Regional PSC NEG<br/>(neg-apigee-psc-<env>)"]
    end

    subgraph ApigeeTenant["Apigee X Tenant Project (esm-<env>-gateway-<sfx>)"]
        SA_IN["Northbound PSC Service Attachment<br/>(google_apigee_instance.service_attachment)"]
        INST["Apigee X Instance (us-central1)<br/>Environment: esmeralda-<env> (INTERMEDIATE)"]
        EP_ATT["Southbound PSC Endpoint Attachment<br/>(*.esmeralda.internal)"]
    end

    subgraph Targets["Downstream Targets"]
        VAI["Vertex AI Agent Runtime<br/>(us-central1-aiplatform.googleapis.com)"]
        MCPS["Internal Entrypoint (10.0.1.2)<br/>-> Cloud Run MCP Servers"]
    end

    DNS -.-> ALB
    ALB -->|"backend-apigee-gateway-<env>"| PSCNEG
    PSCNEG -->|"PSC tunnel"| SA_IN
    SA_IN --> INST
    INST -->|"GoogleAccessToken (sa-esmeralda-apigee-proxy)"| VAI
    INST -->|"GoogleIDToken via PSC"| EP_ATT --> MCPS
```

1. **Apigee Organization & Instance ([`infrastructure/modules/5-workloads/apigee/platform/`](../../../infrastructure/modules/5-workloads/apigee/platform/main.tf)):**
   * Provisions (or adopts via `byo = true`) the `google_apigee_organization` and regional `google_apigee_instance` in `esm-<env>-gateway-<sfx>`.
   * Creates the Apigee environment `esmeralda-<env>` (configurable tier: `BASE`, `INTERMEDIATE`, or `COMPREHENSIVE`) and environment group `envgroup-esmeralda-<env>` listening on `partner.esmeralda.internal`.
   * Creates a regional **Private Service Connect (PSC) NEG** (`neg-apigee-psc-<env>`) pointing to `google_apigee_instance.apigee_instance.service_attachment`, wrapped in a regional `INTERNAL_MANAGED` HTTPS backend service (`backend-apigee-gateway-<env>`).
2. **Entry Point Partner VIP (`10.0.1.3`):**
   * When `partner_access = "apigee"` in `env.yaml`, the Layer 5 `entrypoint` unit attaches `backend-apigee-gateway-<env>` to the URL map for `partner.esmeralda.internal`, injects `Host: partner.esmeralda.internal` via `custom_request_headers`, and exposes the dedicated partner forwarding rule on `10.0.1.3:443` (`partner_ip_address`).
3. **Southbound to the entry point (`apigee/capabilities/partner-mcp`):**
   * A PSC service attachment (`sa-esmeralda-entrypoint-apigee-<env>`, NAT subnet `sb-esmeralda-psc-nat-apigee-<env>` from `psc_nat_cidr`) publishes the entry point forwarding rule; the Apigee endpoint attachment `ea-esmeralda-mcp-<env>` connects to it, and the target server `ts-esmeralda-mcp` reaches it as `apigee-southbound.<domain>` (private zone `apigee-southbound-<env>` in the Shared VPC + Apigee DNS peering zone `dz-esmeralda-mcp-<env>`; the Apigee service agent gets `roles/dns.peer` on the host project). The unit runs after `entrypoint` (Terragrunt `dependencies`), so there is no cycle with the northbound path. `partner-agents` keeps calling Vertex AI directly (`ts-esmeralda-entrypoint`).

---

## 4. Tiered API Products & Consumer Credentials (`apigee/products`)

Before any partner can call an agent or MCP tool, they must subscribe to an **Apigee API Product** provisioned by [`infrastructure/modules/5-workloads/apigee/products/`](../../../infrastructure/modules/5-workloads/apigee/products/main.tf) (live unit `layer-5-workloads/apigee/products`):

| API Product | Proxies Bound | Quota Attribute (`llm_token_quota`) | Purpose |
| :--- | :--- | :--- | :--- |
| **`agent-partner-bronze-<env>`** | `partner-agents` | `10000/minute` (configurable; `50/minute` in quota exhaustion tests) | Entry-level partner tier for B2B orchestrator queries. |
| **`agent-partner-silver-<env>`** | `partner-agents` | `100000/minute` | Mid-tier partner product for high-volume brokerage portals. |
| **`agent-partner-gold-<env>`** | `partner-agents` | `1000000/minute` | Enterprise partner tier. |
| **`mcp-partner-tools-<env>`** | `partner-mcp` | Standard request quota | Partner access to curated MCP tool endpoints (`/partner/v1/mcp/<service>`). |

The `apigee/products` unit also provisions a default smoke-test developer (`partner-smoke-test@example.com`), developer app (`partner-smoke-test`), and consumer credential subscribed to `agent-partner-bronze-<env>` and `mcp-partner-tools-<env>` so `uv run esmeralda verify --env <env> --partner` can run non-interactively out of the box.

---

## 5. Inside the Proxy & Shared Flow Bundles: Step-by-Step Policy Execution

Esmeralda's Apigee implementation is split into **five independent, removable capability bundles** under [`apps/apigee/`](../../../apps/apigee/): three API proxies (`partner-agents`, `partner-mcp` and `llm-gateway`) and two reusable shared flows (`token-quota` and `usage-metering`).

### 5.1 Proxy 1: `partner-agents` (`apps/apigee/proxies/partner-agents/`)

Exposes `/partner/v1/agents/{agent_name}:streamQuery` and `/partner/v1/agents/{agent_name}:query` for partner consumption.

```mermaid
flowchart TD
    subgraph Req["Request Preflow (Northbound -> Southbound)"]
        P1["1. AM-Capture-Request-Start<br/>Record client_received_start_ms & W3C traceparent"]
        P2["2. VAK-Partner-Key (VerifyAPIKey)<br/>Validate x-api-key & load apiproduct.llm_token_quota"]
        P3["3. EV-Agent-Name + KVM-Get-Agent-Config<br/>Read allowed_agents & orchestrator_engine_id from kvm-partner-agents"]
        P4{"4. JS-Validate-Agent<br/>Is agent_name in allowed_agents?"}
        P4_DENY["RF-Agent-Forbidden<br/>Return HTTP 403 (blocks internal specialists)"]
        P5["5. FC-Token-Quota-Enforce<br/>Run shared flow token-quota (EnforceOnly)"]
        P5_DENY["RF-Quota-Exceeded<br/>Return HTTP 429 before calling Vertex AI"]
        P6["6. JS-Rewrite-Target-Path<br/>Rewrite to /v1beta1/.../reasoningEngines/<id>:streamQuery?alt=sse<br/>Set target.copy.pathsuffix = false"]
    end

    subgraph Target["HTTPTargetConnection (Vertex AI Agent Runtime)"]
        T1["Southbound GoogleAccessToken<br/>(sa-esmeralda-apigee-proxy-<env>)<br/>https://us-central1-aiplatform.googleapis.com"]
    end

    subgraph Resp["EventFlow + Response Postflow + PostClientFlow"]
        R1["7. EventFlow / JS-extract-tokens<br/>Parse SSE chunks for usageMetadata.{prompt,candidates,total}TokenCount<br/>Box counts as java.lang.Integer"]
        R2["8. LTQ-count (LLMTokenQuota CountOnly)<br/>Deduct actual llm.totalTokenCount from partner quota"]
        R3["9. FC-Usage-Metering (DataCapture)<br/>Populate dc_prompt_tokens, dc_candidates_tokens, dc_total_tokens, dc_llm_model"]
        R4["10. PostClientFlow: ML-interaction<br/>Write structured JSON event to Cloud Logging<br/>(projects/<gateway>/logs/esmeralda-apigee-interactions)"]
    end

    P1 --> P2 --> P3 --> P4
    P4 -- "No (e.g. ai-coe-mortgage-specialist)" --> P4_DENY
    P4 -- "Yes (cx-mortgage-orchestrator)" --> P5
    P5 -- "Bucket exhausted" --> P5_DENY
    P5 -- "Within budget" --> P6 --> T1
    T1 --> R1 --> R2 --> R3 --> R4
```

#### Key Implementation Details in `partner-agents`:
1. **Blocking Direct Access to Internal Specialists (`KVM-Get-Agent-Config` + `JS-Validate-Agent`):**
   The Terraform module [`infrastructure/modules/5-workloads/apigee/partner-agents/`](../../../infrastructure/modules/5-workloads/apigee/partner-agents/main.tf) populates an encrypted environment KeyValueMap (`kvm-partner-agents`) with `allowed_agents = "cx-mortgage-orchestrator"` and `orchestrator_engine_id = "<numeric-id>"`. When a partner requests `/partner/v1/agents/ai-coe-mortgage-specialist:streamQuery`, `validate-agent.js` sets `agent.validation.Allowed = false` and `RF-Agent-Forbidden` immediately returns `HTTP 403 Forbidden`.
2. **Dynamic Vertex AI Reasoning Engine Target Rewriting (`JS-Rewrite-Target-Path`):**
   In Apigee X, `<HTTPTargetConnection><URL>` does not interpolate arbitrary path variables inside the XML tag. Instead, `rewrite-target-path.js` runs in the Target Preflow, disables default path suffix copying (`context.setVariable("target.copy.pathsuffix", false)`), and rewrites `target.url` to the exact Vertex AI Reasoning Engine endpoint (`https://us-central1-aiplatform.googleapis.com/v1beta1/projects/<project>/locations/us-central1/reasoningEngines/<engine_id>:streamQuery?alt=sse`).
3. **Two-Phase `<LLMTokenQuota>` Enforcement (`EnforceOnly` + `CountOnly`):**
   * **Pre-request (`LTQ-enforce.xml`):** Checks the partner's remaining token budget against `verifyapikey.VAK-Partner-Key.apiproduct.llm_token_quota` *before* forwarding the request to Vertex AI.
   * **Post-response (`extract-tokens.js` + `LTQ-count.xml`):** Extracts `usageMetadata.totalTokenCount` from the SSE stream (`current.event.data`) or JSON body, boxes it as a Java integer (`new java.lang.Integer(Math.round(totalTokens))`), and deducts the actual tokens consumed.

---

### 5.2 Proxy 2: `partner-mcp` (`apps/apigee/proxies/partner-mcp/`)

Exposes the MCP servers listed as partner-exposed in the module catalog (by default `income-verification` and `corporate-email`) at `/partner/v1/mcp/{service}/mcp` for external partner agents:

1. **API Key Verification (`VAK-Verify-Partner-Key`):** verifies the partner's API key and loads the API product's `allowed_mcp_tools` attribute.
2. **Service allowlist (`KVM-Get-MCP-Config` + `JS-Enforce-Tool-Scope`):** reads `allowed_mcp_services` and `apex_domain` from `kvm-partner-mcp`. A path whose service is not in the allowlist (an internal agent host, `legacy-dms`, or anything else behind the entry point) stops at `RF-Not-Found` (`HTTP 404`) and is never routed.
3. **JSON-RPC tool scope (`JS-Enforce-Tool-Scope` + `RF-Tool-Forbidden`):** parses the JSON-RPC 2.0 body; for `tools/call`, `params.name` must be in the product's `allowed_mcp_tools` (`<tool>`, `<service>:<tool>` or `*`), otherwise `HTTP 403`. Discovery (`initialize`, `tools/list`) is always allowed. The products list the real tools of the exposed servers: `verify_applicant` (`income-verification`), `read_email` and, on the premium product, `send_email` (`corporate-email`).
4. **Southbound path to the entry point (`targets/default.xml` + `AM-set-target-host`):** the MCP servers only accept traffic from internal load balancers, so the proxy reaches them through the internal entry point like any agent. The `partner-mcp` unit publishes the entry point forwarding rule to Apigee with a **PSC service attachment** (`sa-esmeralda-entrypoint-apigee-<env>`, own PSC NAT subnet) and an **Apigee endpoint attachment**; the target server `ts-esmeralda-mcp` points at `apigee-southbound.<domain>` (a name covered by the entry point certificate that a private zone of the Shared VPC, DNS-peered to the Apigee organization, resolves to the endpoint attachment IP) and trusts the internal Root CA (keystore `ks-esmeralda-internal-ca`), with strict TLS (`enforce`, hostname verification). `AM-set-target-host` sets `target.header.host` to `<service>.<domain>` (with a target server, Apigee ignores a plain `Host` header) and the request goes to `/mcp` with a Google ID token of the proxy service account (`<GoogleIDToken>` with audience `https://<service>.<domain>`, the Cloud Run custom audience). The proxy service account is the catalog caller `apigee-partner-mcp`, so it gets `roles/run.invoker` on the exposed servers only where `partner_access = "apigee"`.

---

### 5.3 Shared Flows: `token-quota` and `usage-metering`

* **`token-quota` (`apps/apigee/sharedflows/token-quota/`):**
  Encapsulates `AM-Resolve-Quota-Config` (resolving product attribute `llm_token_quota` into count/interval/timeUnit) and `LTQ-Enforce` (`<LLMTokenQuota>` with `EnforceOnly = true`). Requires an `INTERMEDIATE` or `COMPREHENSIVE` Apigee environment.
* **`usage-metering` (`apps/apigee/sharedflows/usage-metering/`):**
  Provisions 5 `google_apigee_data_collector` DataCollectors (`dc_prompt_tokens`, `dc_candidates_tokens`, `dc_total_tokens`, `dc_llm_model`, `dc_consumer_app`) used by `DataCapture`, and ships the shared `ML-interaction` template.

#### Usage records (`ML-interaction`)

Every proxy writes one **`esmeralda.usage`** record per request in `PostClientFlow` to `projects/<gateway-project>/logs/esmeralda-apigee-interactions`. The record follows the usage event contract (`contracts/usage-event.schema.json`), the same schema the `esmeralda` library emits from inside agents, so the BigQuery views join gateway and workload records on `trace_id`. Consumers select the records by `jsonPayload.schema = "esmeralda.usage"`, never by log name.

A MessageLogging template can't compute values, omit empty fields or add numbers, so the `JS-Usage-Context` policy (`usage-context.js`, the same file in every bundle) prepares them: an RFC 3339 `timestamp`, `latency_ms`, the W3C `trace_id` from `traceparent`, the outcome (`http`, `status`, `error_type`) and the JSON fragments for `caller`, `workload`, `tool` and `gen_ai`. It runs after authentication, again at the end of the exchange (response `PostFlow`, final SSE event), and in a `DefaultFaultRule` (`AlwaysEnforce`), so rejected calls (401, 403, 429) are recorded too. Records never contain request or response bodies, credentials or API keys:

| Proxy | `event` | `caller.principal` (`verified: true`) | Extra fields |
| :--- | :--- | :--- | :--- |
| `partner-agents` | `gateway.request` | verified cross-cloud `iss#sub`, else the Apigee developer app name | `workload` = target agent, `gen_ai.usage.*` |
| `partner-mcp` | `gateway.request` | same as `partner-agents` | `workload` = MCP server, `tool` for `tools/call` |
| `llm-gateway` | `llm.call` | the agent's Google identity (ID token `email`, else `sub`) | `gen_ai.request.model`, `response.model`, `usage.*` |

```json
{
  "schema": "esmeralda.usage",
  "schema_version": 1,
  "event": "llm.call",
  "source": "gateway",
  "timestamp": "2026-11-09T02:13:22.123Z",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "caller": {"principal": "sa-cx-mortgage-orchestrator@esm-<env>-cx-agents-<sfx>.iam.gserviceaccount.com", "verified": true},
  "gen_ai": {
    "system": "gcp.vertex_ai",
    "operation.name": "generate_content",
    "request.model": "gemini-2.5-flash",
    "response.model": "gemini-2.5-flash-001",
    "usage.input_tokens": 1200,
    "usage.output_tokens": 120,
    "usage.cached_tokens": 1024,
    "usage.reasoning_tokens": 40
  },
  "http": {"request.method": "POST", "response.status_code": 200},
  "status": "ok",
  "gateway": {"product": "apigee", "name": "llm-gateway"},
  "dimensions": {},
  "latency_ms": 1834
}
```

`usage.output_tokens` includes reasoning tokens and `usage.input_tokens` includes cached tokens (OpenTelemetry GenAI semantics); Gemini reports thinking tokens apart from candidates, so the gateway adds them.

### 5.4 Proxy 3: `llm-gateway`, the agent → model hop (`apps/apigee/proxies/llm-gateway/`)

The partner proxies govern traffic **into** the platform. The `llm-gateway` proxy governs the agents' own **model egress**: with `model_gateway = "apigee"`, every Gemini call an agent makes goes through Apigee instead of straight to `*-aiplatform.googleapis.com`. Apigee then becomes the primary source of token usage and caller identity for chargeback, and enforces a token budget per agent.

```mermaid
sequenceDiagram
    autonumber
    participant Agent as cx-mortgage-orchestrator<br/>(google-genai via esmeralda.models)
    participant AGW as Central Agent Gateway
    participant EP as Entrypoint HTTPS ALB<br/>(apigee.esmeralda.internal)
    participant Apigee as Apigee X Runtime<br/>(llm-gateway proxy)
    participant Vertex as Vertex AI Gemini<br/>(<location>-aiplatform.googleapis.com)

    Agent->>AGW: POST /llm/v1beta1/projects/<p>/locations/global/publishers/google/models/gemini-2.5-flash:streamGenerateContent?alt=sse<br/>Authorization: Bearer <ID token, aud https://apigee.esmeralda.internal>
    AGW->>EP: Registered destination
    EP->>Apigee: Route via PSC NEG
    Note over Apigee: 1. KVM-Get-LLM-Gateway-Config (kvm-llm-gateway)<br/>2. VJ-Verify-Caller-Token: VerifyJWT, Google JWKS, issuer, audience (401)<br/>3. JS-Authorize-LLM-Call: caller, path, project, location, model (403 / 404)<br/>4. LTQ-enforce: LLMTokenQuota per verified caller (429)<br/>5. AM-Strip-Caller-Credentials: the ID token stops here
    Apigee->>Vertex: Same Vertex AI path, GoogleAccessToken (proxy service account)
    Vertex-->>Apigee: SSE chunks with usageMetadata
    Note over Apigee: 6. EventFlow: JS-extract-tokens per chunk<br/>7. Final chunk: LTQ-count, DC-capture-llm-metrics, JS-Usage-Context<br/>8. PostClientFlow: ML-interaction (esmeralda.usage llm.call)
    Apigee-->>Agent: Unbuffered SSE stream
```

#### Agent side: `ESMERALDA_MODEL_GATEWAY_URL`

The agent Terraform units set `ESMERALDA_MODEL_GATEWAY_URL = https://apigee.<domain>/llm` only when `governance.apigee.enabled` and `governance.capabilities.model_gateway = "apigee"`; otherwise the variable is absent and nothing changes. `esmeralda.create_app()` calls `esmeralda.models.route_models(root_agent)`, which replaces every Gemini model name in the agent tree with an ADK `Gemini` model that:

* uses the gateway as `base_url`, so the request path stays the Vertex AI one and the proxy is a pass-through;
* passes `project` (`GOOGLE_CLOUD_PROJECT`) and `location` (`MODEL_LOCATION`, else `global`) explicitly, because google-genai drops them from the path for a custom base URL otherwise;
* authenticates with a Google-signed **ID token** for the agent's **runtime identity**, audience `https://apigee.<domain>`. It is not minted for `SERVICE_ACCOUNT_EMAIL`, which is a shared MCP invoker identity: the gateway attributes tokens per agent.

Model objects (`Gemini(...)`, `LiteLlm(...)`) and inherited models are left as they are.

#### Why an ID token and `VerifyJWT`

Google OAuth2 access tokens are opaque: Apigee has no local policy that can verify them (`VerifyAccessToken` only accepts Apigee-issued tokens), so checking one would take a `ServiceCallout` to Google's tokeninfo endpoint on every model call. A Google ID token is a JWT that `VerifyJWT` checks locally against Google's cached JWKS (signature, `iss = https://accounts.google.com`, `exp`), and its audience binds it to this gateway, so it can't be replayed against another service. The agents already mint ID tokens the same way for MCP servers.

#### Authorization and southbound call

`kvm-llm-gateway`, written by the capability module, holds the allowlists. All of them fail closed (an empty list is written as `none`; there are no wildcard callers):

| KVM entry | Live unit value |
| :--- | :--- |
| `audience` | `https://apigee.<domain>` |
| `allowed_callers` | service accounts of the catalog agents (`kind` `a2a` / `adk`), plus the test VM |
| `allowed_projects` | the CX and AI CoE agent projects (the project in the request path) |
| `allowed_locations` | the env region and `global` |
| `allowed_models` | `gemini-*` |
| `quota_limit`, `quota_timeunit` | tokens per caller per interval (default 1,000,000 per minute) |

The southbound host is derived from the **allowed** location only (`global` → `aiplatform.googleapis.com`, otherwise `<location>-aiplatform.googleapis.com`), so a caller can't steer the proxy to another host. Only `generateContent`, `streamGenerateContent` and `countTokens` on `publishers/google/models/*` are served. The proxy calls Vertex AI with its own access token, so the module grants the proxy service account `roles/aiplatform.user` on the allowed projects.

`streamGenerateContent` is routed to a dedicated SSE target (`response.streaming.enabled`, `EventFlow`): tokens are read per event and metered on the final chunk (`finishReason`), and the stream is never buffered.

---

## 6. How to Enable, Remove, and Verify Apigee X (`esmeralda` CLI)

Every Apigee unit in Layer 5 is controlled declaratively by `governance` in `infrastructure/live/<env>/env.yaml` and includes a Terragrunt `exclude` block so it is automatically skipped when disabled:

```hcl
governance = {
  apigee = {
    enabled          = true
    byo              = false
    environment_type = "INTERMEDIATE" # Required when token_quota = "apigee"
  }
  capabilities = {
    egress_allowlist = "agent_gateway" # Internal East-West governed by Central Agent Gateway
    content_safety   = "agent_gateway" # Model Armor + Semantic Governance (NLC)
    partner_access   = "apigee"        # Enables partner-agents, partner-mcp, and partner VIP (10.0.1.3)
    token_quota      = "apigee"        # Enables token-quota shared flow (LLMTokenQuota)
    usage_metering   = "apigee"        # Enables usage-metering shared flow & DataCollectors
    cross_cloud      = "apigee"        # Required with partner_access: deploys the cross-cloud shared flow the partner proxies call
    model_gateway    = "apigee"        # Agent -> model through llm-gateway (needs usage_metering = "apigee", INTERMEDIATE+)
  }
}
```

The partner proxies call the `cross-cloud` shared flow (`FC-Cross-Cloud`), and Apigee does not deploy a proxy whose shared flow is missing from the environment, so Layer 4 rejects `partner_access = "apigee"` without `cross_cloud = "apigee"`. With no trusted issuers configured, the shared flow rejects every cross-cloud credential (fail closed); API-key partners are unaffected.

`model_gateway` takes `"none"` (default: agents call Vertex AI directly and the workload records are the only token source) or `"apigee"`. Layer 4 rejects `"apigee"` unless `usage_metering = "apigee"` (the proxy writes to its data collectors) and the environment is `INTERMEDIATE` or `COMPREHENSIVE` (`LLMTokenQuota`). The entry point must route `apigee.<domain>` to the Apigee backend, and agents must be allowed to reach that host through the Central Agent Gateway.

### Deploying & Running the Live Partner Conformance Probes

```bash
# 1. Inspect enabled Apigee units in your environment
uv run esmeralda units --env dev

# 2. Apply the Apigee platform, capability bundles, API products, and entrypoint
uv run esmeralda apply layer-5-workloads/apigee/platform --env dev
uv run esmeralda apply layer-5-workloads/apigee/capabilities/token-quota --env dev
uv run esmeralda apply layer-5-workloads/apigee/capabilities/usage-metering --env dev
uv run esmeralda apply layer-5-workloads/apigee/capabilities/partner-agents --env dev
uv run esmeralda apply layer-5-workloads/apigee/capabilities/partner-mcp --env dev
uv run esmeralda apply layer-5-workloads/apigee/capabilities/llm-gateway --env dev   # model_gateway = "apigee"
uv run esmeralda apply layer-5-workloads/apigee/products --env dev
uv run esmeralda apply layer-5-workloads/entrypoint --env dev

# 3. Run the 5 live partner front-door conformance probes (including quota exhaustion)
ESMERALDA_TEST_QUOTA_EXHAUSTION=1 uv run esmeralda verify --env dev --partner
```

`uv run esmeralda verify --env dev --partner` (backed by [`scripts/test_partner.sh`](../../../scripts/test_partner.sh)) executes the live probes from the private test VM against `https://partner.esmeralda.internal` (`10.0.1.3`):

1. **Probe 1 (`HTTP 401`):** Unauthenticated partner request (no `x-api-key`) is rejected by `VAK-Partner-Key`.
2. **Probe 2 (`HTTP 403`):** Partner request with a valid API key attempting to call internal specialist `/partner/v1/agents/ai-coe-mortgage-specialist:streamQuery` is blocked by `JS-Validate-Agent`.
3. **Probe 3 (`HTTP 403`):** Partner request attempting to invoke an unallowlisted MCP tool via `/partner/v1/mcp/income-verification` is blocked by `JS-Validate-MCP-Tool`.
4. **Probe 4 (`HTTP 200`):** Partner request to `/partner/v1/agents/cx-mortgage-orchestrator:streamQuery` streams Julian Sterling's underwriting response end-to-end (Apigee X → `cx-mortgage-orchestrator` → GEAP Agent Identity → Central Agent Gateway → `ai-coe-mortgage-specialist` → MCP servers) and records token metrics.
5. **Probe 5 (`HTTP 429`):** Repeated partner calls exhaust the `llm_token_quota` budget on the product and are rejected with `HTTP 429 Too Many Requests` before reaching Vertex AI.
6. **Probe 6 (`HTTP 200`):** MCP `tools/list` on `/partner/v1/mcp/income-verification/mcp` returns the server's tools (`verify_applicant`) through the southbound path.
7. **Probe 7 (`HTTP 200`):** an allowed MCP `tools/call` (`verify_applicant` for Julian Sterling) is answered by `income-verification`.

The probes are defined once, in the partner scenario catalog [`tests/partner/scenarios.py`](../../../tests/partner/scenarios.py) (every scenario with a `probe` number), and sent from the VM by [`tests/partner/probe.py`](../../../tests/partner/probe.py) (stdlib Python, no packages to install). Probe 5 only runs with `ESMERALDA_TEST_QUOTA_EXHAUSTION=1`.

---

## 7. Load Testing Through the Partner Front Door

`make load-test-partner` drives a steady, small partner workload through Apigee, for example to fill the Apigee Analytics dashboards and the token-quota and usage-metering views before a demo:

```bash
make load-test-partner ENV=dev                   # 2 users, 5 minutes
make load-test-partner ENV=dev USERS=1 DURATION=3m
make load-test-partner ENV=dev INCLUDE_QUOTA=1   # adds quota exhaustion (burns the product quota)
```

[`scripts/partner_load_test.sh`](../../../scripts/partner_load_test.sh) resolves the test VM, the entry point, the internal CA and the partner key the same way as `esmeralda verify --partner`, and runs [Locust](https://locust.io) **on the test VM** over IAP SSH, because `partner.esmeralda.internal` only resolves inside the VPC. On first use it installs Locust on the VM in a cached virtual environment with `uv`, which needs outbound HTTPS from the VM (for example through Cloud NAT). The CSV and HTML reports are copied back to `tests/load_test/.results/partner/` (gitignored).

The traffic mix is the same scenario catalog as the conformance probes: one weighted Locust task per scenario with `weight > 0`. A request counts as a success when its status is in the scenario's expected set, so the deliberate `401` and `403` rejections show up as successes under their own names, next to the real orchestrator answers:

| Scenario | Proxy | Expected | What the console shows |
| :--- | :--- | :--- | :--- |
| `agents-no-key`, `agents-invalid-key`, `mcp-no-key` | `partner-agents`, `partner-mcp` | `401` | `VerifyAPIKey` faults in error code analysis |
| `agents-specialist-card-blocked`, `agents-specialist-stream-blocked` | `partner-agents` | `403`/`404` | `RF-Agent-Not-Allowed`: internal specialists are never exposed |
| `mcp-restricted-tool-call` | `partner-mcp` | `403` | `RF-Tool-Forbidden`: tool scope enforced at the gateway |
| `mcp-tools-list` | `partner-mcp` | `200` + `verify_applicant` | MCP discovery through the southbound path to the entry point |
| `mcp-tools-call-allowed` | `partner-mcp` | `200` + "Sterling" | An allowed tool (`verify_applicant`) answered by `income-verification` |
| `orchestrator-income`, `orchestrator-employment`, `orchestrator-household` | `partner-agents` | `200` + "Sterling" | SSE answers, time to first event and stream total, token counts |
| `orchestrator-quota-exhaustion` (opt-in) | `partner-agents` | `429` | `LLMTokenQuota` rejections once the product budget is spent |

Each simulated user sends a synthetic end-user id (`x-esmeralda-partner-user` header and `user_id` in the query body), a W3C `traceparent` (logged by `ML-interaction`), and an `x-esmeralda-load-run` header with the run id. Keep the load small: the specialist runs a single instance and an answer takes about 25 seconds.

**What to show in the Google Cloud console during a run** (Apigee, environment `esmeralda-<env>`):

1. **Proxy development → API proxies → `partner-agents` → Debug:** start a debug session, then open a `200` transaction to walk through `VAK-Verify-Partner-Key`, `LTQ-enforce`, the `sse` target and the EventFlow token extraction, and a `403` transaction that stops at `RF-Agent-Not-Allowed`. On `partner-mcp`, a `403` transaction stops at `RF-Tool-Forbidden`, and a `200` one (`tools/list` or `verify_applicant`) shows `KVM-Get-MCP-Config`, `JS-Enforce-Tool-Scope`, then `AM-set-target-host` and the call to the `ts-esmeralda-mcp` target server.
2. **Analytics → API metrics → API proxy performance:** traffic and latency per proxy (`partner-agents`, `partner-mcp`). **Error code analysis** splits the expected `401`/`403` from real errors. On pay-as-you-go organizations these dashboards need the environment's Analytics add-on: set `governance.apigee.enable_analytics = true` in `env.yaml` (on in dev, off in prd; it adds a per-call cost) and apply `apigee/platform`. Data appears after several minutes; Debug works without the add-on.
3. **Analytics → Developer metrics → Developer apps:** traffic attributed to the `partner-smoke-test` app.
4. **Distribution → API products → `esmeralda-partner-standard`:** the `allowed_mcp_tools` (`verify_applicant`, `read_email`) and LLM token quota attributes the gateway enforces.
5. **Management → Environments → `esmeralda-<env>` → Target servers:** `ts-esmeralda-mcp` (host `apigee-southbound.<domain>`, TLS with the internal CA truststore); **Management → Endpoint attachments** shows `ea-esmeralda-mcp-<env>` connected to the entry point service attachment, and **Management → DNS peering** the zone `dz-esmeralda-mcp-<env>`.
6. **Logging → Logs Explorer:** log `esmeralda-apigee-interactions` in the gateway project (`logName="projects/<gateway-project>/logs/esmeralda-apigee-interactions"`), one `esmeralda.usage` record per partner call of both proxies with the verified caller (`caller.principal`, never the API key), the target workload, the MCP tool, the outcome and token counts. `ML-interaction` writes with the proxy runtime service account (`roles/logging.logWriter`); its message template uses single-quoted literals (`{firstnonnull(usage.latency_ms,'0')}`), because a double-quoted literal makes the JSON payload invalid and Cloud Logging drops the entry. In BigQuery, the records land in `esmeralda_usage_<env>` and feed `vw_partner_apigee_chargeback` (partner chargeback by verified partner team) and `vw_usage_billable`.

> `make load-test` (Locust against the Vertex AI API) bypasses Apigee: its traffic never appears in Apigee Analytics and does not consume partner token quota. Use `make load-test-partner` for anything Apigee should see.

**Adding a probe or a load scenario:** append one `Scenario` to [`tests/partner/scenarios.py`](../../../tests/partner/scenarios.py) with its path, headers, body, expected statuses and weight (give it a `probe` number to also run it in `esmeralda verify --partner`, or `destructive=True` to keep it opt-in). Neither the probe runner, the Locust file nor the scripts change; `make test-apigee` checks the catalog offline.

---

**Next Section:** Now that we have seen how requests enter the platform—both internally via `direct` / `envoy` and externally via **Apigee X**—continue to **[2.0 Governance, Identity & AgentOps — Overview & Release Lifecycle](../3-agentops-and-lifecycle/index.md)** to see how the **Central Agent Gateway**, **SPIFFE Agent Identity**, and **Semantic Governance** govern every internal hop.
