# 2.5 Usage Telemetry Contract

Chargeback across teams, frameworks and clouds only works if every producer describes usage the
same way. Esmeralda therefore publishes a **contract**, not an implementation: the
`esmeralda.usage` record, defined by
[`contracts/usage-event.schema.json`](https://github.com/google/esmeralda/blob/main/contracts/usage-event.schema.json).
Any producer can implement it: the `esmeralda` Python library, a gateway access log, an
OpenTelemetry Collector processor, or code in another language.

## The record

One record per **model call** (`llm.call`), **tool call** (`tool.call`), **agent invocation**
(`agent.invocation`) or **gateway request** (`gateway.request`). Field names follow the
[OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
wherever they exist.

```json
{
  "schema": "esmeralda.usage",
  "schema_version": 1,
  "event": "llm.call",
  "timestamp": "2026-01-15T14:03:07.412Z",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "source": "workload",
  "workload": {"name": "ai-coe-mortgage-specialist", "team": "ai-coe", "runtime": "agent_engine",
               "framework": "adk", "cloud": "gcp"},
  "caller": {"verified": false,
             "declared": {"project_id": "example-cx-project", "agent_name": "cx_mortgage_orchestrator"}},
  "gen_ai": {"system": "gcp.vertex_ai", "operation.name": "generate_content",
             "request.model": "gemini-flash", "response.model": "gemini-flash-001",
             "usage.input_tokens": 1840, "usage.output_tokens": 312,
             "usage.cached_tokens": 1024, "usage.reasoning_tokens": 96,
             "agent.name": "mortgage_specialist", "conversation.id": "session-julian-sterling-001"},
  "status": "ok",
  "dimensions": {"loan_product": "fixed_30y"},
  "latency_ms": 1432.5
}
```

| Field | Meaning |
|---|---|
| `schema`, `schema_version` | Record type and integer contract version. Sinks and metrics filter on `jsonPayload.schema = "esmeralda.usage"`, **never on a log name** |
| `source` | `workload` (the agent or service itself) or `gateway` (a gateway on the hop) |
| `workload` | Who served the call: `name`, `team`, `runtime` (`agent_engine`, `cloud_run`, `gke`, `external`), `framework`, `cloud` |
| `caller` | Who called. `principal` / `team` count for chargeback only with `verified: true` (from authentication). `declared` is caller-supplied context (cost centre, country, calling project and agent) |
| `gen_ai` | OTel GenAI attributes without the `gen_ai.` prefix. `usage.input_tokens` **includes** cached tokens and `usage.output_tokens` **includes** reasoning tokens; `usage.cached_tokens` and `usage.reasoning_tokens` are subsets |
| `tool` | `name`, `server`, `call_id`, `status` (`ok` / `error`), `error_type`, `duration_ms` |
| `gateway`, `http` | Gateway records: `product` (`apigee`, `envoy`, `agent_gateway`, `direct`), proxy name, HTTP method, status code, route |
| `dimensions` | Business dimensions (strings). Consumers accept a key only if the catalog registers it |
| `trace_id`, `span_id` | W3C ids. Gateway and workload records of one request join on `trace_id` |

`agent.invocation` token counts are a roll-up of the invocation's model calls. Use them for
per-invocation budgets and alerts; chargeback sums `llm.call` (or gateway) records.

One example per event type lives in
[`contracts/examples/`](https://github.com/google/esmeralda/tree/main/contracts/examples).

### Versioning

The version is an integer inside each record, not a `v1/` module or folder. Additive changes
(new optional fields, new enum values) keep `schema_version`; only a breaking change bumps it.
Producers emit only fields the schema defines; anything business-specific goes in `dimensions`.
The full rules are in
[`contracts/README.md`](https://github.com/google/esmeralda/blob/main/contracts/README.md).

### Secrets stay out

Records never carry credentials. Before a record leaves the process, `esmeralda.usage` drops every
key that looks like a credential or token (`access_token`, `authorization`, `api_key`, the
delegated user credential `user_auth_token` or whatever `USER_AUTH_ID` names, …) and masks values
that look like bearer tokens, OAuth access tokens, JWTs or API keys, including inside error
messages. Tool and model errors are recorded by **type** (`TimeoutError`), never by message.

The delegated user credential is also masked **by value**: the ADK plugin passes the invocation's
own credential to `esmeralda.user_credential.redact`, so it is removed even when it has no
recognizable shape, and the credential headers listed in `ESMERALDA_USER_CREDENTIAL_TARGETS` are
never emitted as keys. Manual producers can do the same with `secrets=[...]` on any `record_*`
call.

## Producing records

All Python producers go through `esmeralda.usage`. Records are written as one JSON line on stdout
by default, which Cloud Logging stores as `jsonPayload` on Agent Runtime, Cloud Run and GKE. Set
`ESMERALDA_USAGE_TRANSPORT=otel` (or `stdout,otel`) to emit them as OpenTelemetry log records
instead, for an OTLP collector on another cloud.

`workload` comes from the environment:

| Variable | Field | Default |
|---|---|---|
| `ESMERALDA_WORKLOAD_NAME` | `workload.name` | the agent name, else `AGENT_NAME`, `OTEL_SERVICE_NAME`, `K_SERVICE` |
| `ESMERALDA_TEAM` | `workload.team` | unset |
| `ESMERALDA_RUNTIME` | `workload.runtime` | detected: Agent Runtime, Cloud Run, GKE, else `external` |
| `ESMERALDA_FRAMEWORK` | `workload.framework` | set by the producer (`adk` for the plugin) |
| `ESMERALDA_CLOUD` | `workload.cloud` | `gcp` |

### ADK agents

Nothing to do: `esmeralda.create_app(root_agent)` installs `EsmeraldaTelemetryPlugin`, whose
callbacks emit an `llm.call` per model response (token usage including cached and reasoning
tokens, latency, finish reason), a `tool.call` per tool call (status, duration, MCP server when
known) and an `agent.invocation` per run (latency and token roll-up). `caller.declared` comes from
the caller context (`temp:caller_context` state or A2A metadata) with `caller.verified: false`,
because that context is self-declared.

While the Layer 4 sinks, metrics and dashboards still key on them, the plugin also writes the
legacy `genai_token_consumption` and `mcp_tool_execution` events. Set
`ESMERALDA_LEGACY_TELEMETRY_EVENTS=false` to stop them once your views read `esmeralda.usage`.

### Any OpenTelemetry-instrumented framework

LangGraph, LangChain, CrewAI or custom code instrumented with the OTel GenAI conventions only needs
the span adapter on its `TracerProvider`:

```python
from opentelemetry.sdk.trace import TracerProvider

from esmeralda.telemetry import BaggageSpanProcessor
from esmeralda.usage import UsageSpanProcessor

provider = TracerProvider()
provider.add_span_processor(BaggageSpanProcessor())  # optional: caller.* attributes from baggage
provider.add_span_processor(UsageSpanProcessor(framework="langgraph"))
```

`UsageSpanProcessor` maps finished spans by `gen_ai.operation.name`: `generate_content`, `chat` and
`text_completion` become `llm.call`, `execute_tool` becomes `tool.call`, and `invoke_agent` becomes
`agent.invocation`. Spans without a GenAI operation name are ignored. `caller.*` span attributes
become `caller.declared`, and `esmeralda.dimension.<key>` attributes become `dimensions`.

!!! warning "One producer per process"
    Use either the ADK plugin or the span adapter in an ADK process, not both: each records the
    same calls.

### Manual API

For code with neither ADK nor OTel instrumentation, call the typed builders:

```python
from esmeralda import usage

usage.record_llm_call(
    request_model="gemini-flash",
    input_tokens=1840,
    output_tokens=312,
    cached_tokens=1024,
    latency_ms=1432.5,
    dimensions={"loan_product": "fixed_30y"},
)
usage.record_tool_call(name="verify_income", server="income-verification", status="ok", duration_ms=218.4)
usage.record_agent_invocation(agent_name="mortgage_specialist", latency_ms=4105.0)
```

The module functions use a process-wide `UsageRecorder`; create your own to choose the transport,
the workload or default dimensions:

```python
recorder = usage.UsageRecorder(
    usage.OTelLogsTransport(),
    workload=usage.Workload("loan-scoring", team="risk", runtime="external", framework="custom", cloud="aws"),
    dimensions={"country": "us"},
)
recorder.record_llm_call(request_model="gemini-flash", input_tokens=10, output_tokens=2)
```

## Validation

The test suite validates the schema (JSON Schema draft 2020-12), every example, and every record
the library produces (manual API, span adapter, and the ADK plugin inside a real ADK runner)
against the contract.
