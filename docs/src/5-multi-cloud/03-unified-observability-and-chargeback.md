# 5.3 Unified Observability & Chargeback

In **[2.3 FinOps, Telemetry & Cross-Team Chargeback](../3-agentops-and-lifecycle/03-centralized-monitoring-and-dashboards.md)**, the CFO could see what Julian Sterling's mortgage inquiry cost, because every agent wrote structured telemetry into Cloud Logging and Layer 4 sank it into BigQuery. That worked because every producer was an Esmeralda agent on Google Cloud, using the same library.

Multi-cloud breaks that assumption three ways at once:

* The underwriting agent on another cloud is written in a **different framework**, maybe a different language.
* Its telemetry lands in a **different cloud's** logging and tracing stack.
* Its token usage is billed by a **different model provider**.

If each producer describes usage its own way, FinOps ends up writing one parser per cloud, per framework, per gateway, and the numbers never quite add up. Esmeralda's answer is a **contract**: one normalized record that every producer emits, wherever it runs.

---

## 1. The Normalized Schema: `esmeralda.usage`

Every unit of usage, from any producer, is one `esmeralda.usage` record. There are four event types:

| Event | One Record Per | Typical Producer |
| :--- | :--- | :--- |
| `llm.call` | Model call | The workload (agent library, span adapter) |
| `tool.call` | Tool call | The workload |
| `agent.invocation` | Agent run (with a token roll-up for budgets and alerts) | The workload |
| `gateway.request` | Request crossing a gateway | Apigee, Envoy, or a mapped gateway log |

Field names follow the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) wherever they exist, so any OTel-instrumented framework already produces most of the data. Two fields make the record work across clouds:

* **`workload.cloud`** (`gcp`, `aws`, `azure`, `on_prem`, `other`) and **`workload.runtime`** (`agent_engine`, `cloud_run`, `gke`, `external`) say *where* the work ran.
* **`caller`** says *who* asked for it. `caller.principal` and `caller.team` count for chargeback **only** when `caller.verified` is `true`, meaning they came from authentication. Anything the caller merely claimed goes in `caller.declared`.

Each record carries `schema: "esmeralda.usage"` and an integer `schema_version`. Additive changes keep the version; only a breaking change bumps it. Consumers select records by `schema`, never by log name, which is what lets records from any cloud and any transport land in the same tables. Records never contain credentials, tokens or the delegated user credential, in any field.

> [!NOTE]
> The `esmeralda.usage` contract (`contracts/usage-event.schema.json`), its Python producers and the Layer 4 usage views are in open pull requests. On `main`, agents emit the earlier `genai_token_consumption` and `mcp_tool_execution` events with OpenTelemetry baggage, read by `vw_monthly_agent_chargeback` (see [2.3](../3-agentops-and-lifecycle/03-centralized-monitoring-and-dashboards.md)). See [5.5 Status & Roadmap](./05-status-and-roadmap.md).

---

## 2. Producers on Every Cloud

The contract is the same everywhere; only the producer changes.

| Producer | Where It Runs | How It Emits `esmeralda.usage` |
| :--- | :--- | :--- |
| **`esmeralda` library, ADK plugin** | Any Python ADK agent, on any cloud | `esmeralda.create_app(root_agent)` installs a telemetry plugin that emits `llm.call`, `tool.call` and `agent.invocation` records. |
| **`esmeralda` library, OTel GenAI span adapter** | Any OTel-instrumented Python framework (LangGraph, LangChain, CrewAI, custom code) | `UsageSpanProcessor` on the `TracerProvider` maps finished GenAI spans to records. |
| **`esmeralda` library, manual API** | Python code without ADK or OTel instrumentation | Typed builders such as `record_llm_call()` and `record_tool_call()`. |
| **OpenTelemetry Collector** | Any language, any cloud | Receives OTLP from the workload and forwards records (or maps GenAI spans to records) to Google Cloud. |
| **Apigee** | Inbound gateway | The `ML-interaction` message-logging policy writes gateway records with the verified caller. |
| **Envoy** | Gateway adapter | A JSON access-log format that matches the schema. |
| **Fixed-format gateways** (such as Agent Gateway logs) | Google Cloud | Mapped to the schema by a BigQuery view. |

For a Python agent on another cloud, the span adapter is usually all it takes. The workload declares where it runs through environment variables (`ESMERALDA_CLOUD=aws`, `ESMERALDA_RUNTIME=external`, `ESMERALDA_TEAM=example-cloud-underwriting`) and switches the transport to OpenTelemetry logs so a collector can ship the records:

```python
from opentelemetry.sdk.trace import TracerProvider

from esmeralda.usage import UsageSpanProcessor

# Environment: ESMERALDA_USAGE_TRANSPORT=otel, ESMERALDA_CLOUD=aws, ESMERALDA_RUNTIME=external
provider = TracerProvider()
provider.add_span_processor(UsageSpanProcessor(framework="langgraph"))
```

For workloads in other languages, an **OpenTelemetry Collector** is the bridge. The workload exports standard OTLP traces and logs to a collector on its own cloud; the collector exports to Google Cloud's OTLP endpoint (the Telemetry API, `telemetry.googleapis.com`), authenticating keylessly with a [Workload Identity Federation credential configuration](https://cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds) instead of a downloaded key. The federated principal needs only permission to write telemetry.

```yaml
# Illustrative OpenTelemetry Collector config on the other cloud.
# GOOGLE_APPLICATION_CREDENTIALS points at a Workload Identity Federation
# credential configuration file (no service account key).
extensions:
  googleclientauth: {}

receivers:
  otlp:
    protocols:
      http: {}

exporters:
  otlphttp:
    endpoint: https://telemetry.googleapis.com
    encoding: proto
    auth:
      authenticator: googleclientauth

service:
  extensions: [googleclientauth]
  pipelines:
    traces: { receivers: [otlp], exporters: [otlphttp] }
    logs:   { receivers: [otlp], exporters: [otlphttp] }
```

```mermaid
flowchart LR
    subgraph OC["Another cloud"]
        PY["Python agent<br/>(esmeralda span adapter)"]
        ANY["Agent in any language<br/>(OTel GenAI instrumentation)"]
        COLL["OpenTelemetry Collector<br/>(WIF credential, no keys)"]
    end

    subgraph GC["Google Cloud governance project"]
        TEL["Telemetry API / Cloud Logging"]
        SINK["Log sink<br/>jsonPayload.schema = esmeralda.usage"]
        BQ[("BigQuery<br/>usage events")]
        V["Views: normalized, billable,<br/>chargeback, request-level"]
    end

    subgraph ESM["Esmeralda workloads & gateways"]
        AG["Agents on Agent Runtime<br/>(ADK plugin, stdout JSON)"]
        AX["Apigee<br/>(ML-interaction)"]
    end

    PY -->|"OTLP logs"| COLL
    ANY -->|"OTLP traces + logs"| COLL
    COLL -->|"OTLP over HTTPS"| TEL
    AG -->|"structured JSON"| TEL
    AX -->|"message logging"| TEL
    TEL --> SINK --> BQ --> V
```

> [!NOTE]
> On Google Cloud, the default transport is one JSON line on stdout, which Cloud Logging stores as `jsonPayload` on Agent Runtime, Cloud Run and GKE. OTLP through a collector is the multi-cloud transport: the record is the same, only the envelope changes. A tested reference collector configuration (including a processor that maps GenAI spans to records for non-Python workloads) is on the roadmap; until then, treat the snippet above as a starting point and validate that the records arrive with `schema = "esmeralda.usage"` in the governance project.

---

## 3. Landing in BigQuery

Layer 4's hub-and-spoke telemetry model extends naturally: records from Google Cloud spokes and from other clouds all reach the governance project, and one sink keyed on `jsonPayload.schema = "esmeralda.usage"` streams them into a BigQuery usage table. Views on top of it do the rest:

1. **Normalize.** Gateway and workload records, from any producer, are flattened into one shape.
2. **Join on `trace_id`.** A single request usually leaves several records: an Apigee `gateway.request` on the way in, `llm.call` and `tool.call` records from the specialist, and maybe an `llm.call` from the caller on its own cloud. W3C trace context propagation ([5.2 §3](./02-cross-cloud-connectivity.md#3-a2a-and-mcp-the-interoperable-protocols)) gives them all the same `trace_id`.
3. **Pick the billing source per hop.** When a gateway is on the hop, its record is the primary source for tokens and caller identity. Workload records add per-tool and sub-agent detail, and are the floor on `direct` hops.
4. **Accept only registered dimensions.** Business keys such as `loan_product` are accepted only if the catalog's `policy.dimensions` registers them.

---

## 4. Chargeback by Verified Team

The goal of all this plumbing is a simple, defensible statement: **the team whose verified identity triggered the work pays for it**, wherever the work ran.

| Question | Answered From |
| :--- | :--- |
| Who called? | `caller.principal` with `caller.verified = true` (an `iss#sub`, a federated principal, a SPIFFE ID, an Apigee app or a service account) |
| Which team pays? | The catalog's `callers` / targets mapping of that principal to a team, compiled into an attribution table |
| Which cost centre? | The caller's declared cost centre, only if the catalog allows it for that caller (`policy.attribution.declared_mismatch` decides otherwise) |
| What did it cost? | Token counts from `llm.call` or `gateway.request` records (`usage.input_tokens` includes cached tokens; `usage.output_tokens` includes reasoning tokens) |
| Where did it run? | `workload.cloud`, `workload.runtime`, `workload.team` |

A month-end chargeback row for the worked example in [5.4](./04-bringing-an-agent-from-another-cloud.md) might read:

| Calling Team (verified) | Serving Team | Serving Cloud | Requests | Input Tokens | Output Tokens |
| :--- | :--- | :--- | ---: | ---: | ---: |
| `example-cloud-underwriting` | `ai-coe` | `gcp` | 1,240 | 4,860,800 | 812,200 |
| `cx` | `ai-coe` | `gcp` | 9,310 | 36,494,000 | 6,098,050 |

*(Synthetic numbers.)*

Two alarms keep the view honest:

* **Unattributed usage.** Any record whose principal is not in the attribution table counts as unattributed. The expected value is zero; an alert fires when it is ever greater.
* **Declared vs. verified mismatch.** A caller declaring a cost centre its team does not own is rejected or billed to its own team, per policy, and surfaced for review.

> [!IMPORTANT]
> Verified attribution needs every caller to present a **distinct** verified identity. On paths where the callee only sees a gateway's identity (such as a target behind Apigee), the gateway's record is the attribution source. Self-declared context (OpenTelemetry baggage such as `caller.project_id`, A2A caller metadata) stays useful for dashboards but never decides who pays.

---

**Next Subsection:** Identity, connectivity and telemetry are in place. Let's put them together and onboard a real (synthetic) agent from another cloud, step by step. Continue to **[5.4 Bringing an Agent from Another Cloud, End to End](./04-bringing-an-agent-from-another-cloud.md)**.
