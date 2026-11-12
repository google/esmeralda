# 4.2 Package & CLI Reference (`esmeralda-platform`)

## One Audited Contract for Every Runtime Hook and Operator Command

This page is the complete API and CLI reference for `esmeralda` (`esmeralda-platform` on PyPI, [source](../../../packages/esmeralda/src/esmeralda/__init__.py)). While the [usage guide](guide.md) walks through recipes by task, this reference documents the exact contract of every module, function, class, CLI flag, telemetry JSON payload, and environment variable so platform engineers and application developers have a single authoritative specification.

---

## Why We Built It This Way (Key Decisions & Rationale)

| Decision | Alternative Considered | Why We Chose This |
| :--- | :--- | :--- |
| **Small, Focused Submodules (`app`, `lifecycle`, `plugin`, `auth`, `auth_manager`, `mcp`, `mcp_authz`, `a2a`, `ops`)** | A single monolithic utility file | Keeps runtime dependencies minimal inside agent containers while isolating operational tooling (`esmeralda.ops`, `fastmcp`) to developer and CI workflows. |
| **Plan-First Destroy Guard in `esmeralda apply` (`infra.apply_unit`)** | Running raw `terragrunt apply` directly | Inspects the saved plan JSON before applying and refuses with exit code `2` whenever `destroy > 0` unless `--allow-destroy` is explicitly passed. |
| **JSON-First `esmeralda.ops` Functions (`--json`)** | Parsing unstructured human text in scripts and MCP tools | Every CLI subcommand (`status`, `doctor`, `units`, `plan`, `apply`, `verify`, `logs`, `contracts`, `guard`) and every `esmeralda mcp` tool shares the same structured dict return values and `OpsError` hints. |

---

## Top-level API

`import esmeralda` exposes:

| Name | Defined in | Summary |
|---|---|---|
| `prepare(steps=PREPARE)` | `esmeralda.lifecycle` | Runs the prepare steps |
| `finalize(steps=FINALIZE)` | `esmeralda.lifecycle` | Runs the finalize steps |
| `create_app(root_agent, *, name="agent", plugins=())` | `esmeralda.app` | ADK `App` with `EsmeraldaTelemetryPlugin` |
| `EsmeraldaTelemetryPlugin` | `esmeralda.plugin` | The per-request ADK plugin |

## `esmeralda.app`

### `create_app(root_agent, *, name="agent", plugins=()) -> google.adk.apps.App`

Returns an ADK `App` whose plugins are `EsmeraldaTelemetryPlugin()` followed by `plugins`.

| Parameter | Description |
|---|---|
| `root_agent` | The agent (or any ADK node) the app serves |
| `name` | App name. Must match the agent package directory and `--gemini_enterprise_app_name` (both `agent` in this repo) |
| `plugins` | Additional ADK plugins, run after `EsmeraldaTelemetryPlugin` |

## `esmeralda.lifecycle`

Process lifecycle. Each step is a function without arguments (`Step = Callable[[], None]`), is idempotent, and raises on failure.

### `prepare(steps=PREPARE) -> None`

Runs `steps` in order. Call it once, at the top of the agent package, before the agent definition is imported.

### `finalize(steps=FINALIZE) -> None`

Runs `steps` in order. Cheap to call repeatedly: `EsmeraldaTelemetryPlugin` calls it at the start of every run.

### `PREPARE`

Default prepare steps, in this order:

| Step | What it does |
|---|---|
| `log_egress_proxy()` | Logs the `*_proxy` environment variables the runtime advertises for gateway egress |
| `default_environment()` | Sets `GOOGLE_CLOUD_PROJECT` from Application Default Credentials (only if unset; a warning if there are no credentials), and defaults `GOOGLE_CLOUD_LOCATION=global`, `GOOGLE_GENAI_USE_VERTEXAI=True`, `GRPC_DNS_RESOLVER=native`. Never overrides an explicit value |
| `genai_over_httpx()` | Makes google-genai use httpx instead of aiohttp. aiohttp ignores `https://` proxies, so the Gemini and Vertex session calls would bypass the egress proxy and fail |
| `aiohttp_trust_env()` | Makes `aiohttp.ClientSession` default to `trust_env=True`, so other aiohttp users honor the proxy env. No-op if aiohttp isn't installed |
| `genai_client_defaults()` | On every `google.genai.Client`: if `MODEL_LOCATION` is set, uses it as the location; otherwise defaults the location to `global`. Defaults `project` to `GOOGLE_CLOUD_PROJECT` |

### `FINALIZE`

| Step | What it does |
|---|---|
| `baggage_span_processor()` | Registers `BaggageSpanProcessor` on the global tracer provider, once per provider. If there is no OpenTelemetry SDK provider, logs a warning once and does nothing |

## `esmeralda.plugin`

### `class EsmeraldaTelemetryPlugin(google.adk.plugins.base_plugin.BasePlugin)`

```python
EsmeraldaTelemetryPlugin(*, agent_name=None, finalize_steps=lifecycle.FINALIZE, emitter=None,
                         recorder=None, legacy_events=None)
```

| Parameter | Description |
|---|---|
| `agent_name` | `workload.name` of usage records (unless `ESMERALDA_WORKLOAD_NAME` is set) and `agent_id` in legacy events. Defaults to the `AGENT_NAME` environment variable, then `unknown_agent` |
| `finalize_steps` | The steps passed to `finalize()` at the start of each run |
| `emitter` | A `TelemetryEmitter` for the legacy events. Defaults to one on `usage.transport_from_env()` (stdout) |
| `recorder` | The `usage.UsageRecorder` for `esmeralda.usage` records. Defaults to one on the emitter's transport, with `workload` from the environment and framework `adk` |
| `legacy_events` | Also write the legacy `genai_token_consumption` / `mcp_tool_execution` events. Defaults to `ESMERALDA_LEGACY_TELEMETRY_EVENTS` (on) |

Plugin name: `esmeralda_telemetry`. Callbacks:

| Callback | Behavior |
|---|---|
| `before_run_callback` | Runs `finalize`. Reads the caller context (`context.from_invocation`) and, if present, attaches it as baggage for the run. Starts the invocation's latency clock and token roll-up |
| `after_run_callback` | Emits `agent.invocation` (latency, token roll-up, `status=error` if a model call failed) and detaches the baggage of that invocation |
| `before_tool_callback` | Records the tool start time |
| `after_tool_callback` | Emits `tool.call` with `status=ok` (and legacy `mcp_tool_execution` with `status=SUCCESS`) |
| `on_tool_error_callback` | Emits `tool.call` with `status=error` and the exception type (and legacy `mcp_tool_execution` with `status=ERROR` and a redacted `error_reason`). Returns `None`, so the agent's own error handling still runs |
| `before_model_callback` | Records the model start time and the requested model |
| `after_model_callback` | For complete (non-partial) responses with token counts, emits `llm.call` (and legacy `genai_token_consumption`) |
| `on_model_error_callback` | Emits `llm.call` with `status=error` and the exception type |

Usage records carry `caller.declared` = the caller context (`project_id`, `agent_name`) with `caller.verified=false`, because that context is self-declared. Token counts follow OpenTelemetry GenAI semantics: `usage.input_tokens` = prompt + tool-use prompt tokens (cached included), `usage.output_tokens` = candidate + reasoning tokens.

None of the callbacks change the request or the response. Telemetry failures are logged and never break the run. Per-invocation bookkeeping is bounded (1024 entries) for runs that never reach `after_run`.

## `esmeralda.usage`

Producer of the `esmeralda.usage` contract ([`contracts/usage-event.schema.json`](https://github.com/google/esmeralda/blob/main/contracts/usage-event.schema.json); see [2.5 Usage Telemetry Contract](../3-agentops-and-lifecycle/05-usage-telemetry-contract.md)). Framework-agnostic: no ADK import.

### Records

| Function / method | Event |
|---|---|
| `record_llm_call(*, request_model=None, response_model=None, system=None, input_tokens=None, output_tokens=None, cached_tokens=None, reasoning_tokens=None, finish_reasons=None, operation_name="generate_content", agent_name=None, conversation_id=None, status="ok", error_type=None, latency_ms=None, caller=None, dimensions=None, span=None, timestamp=None)` | `llm.call` |
| `record_tool_call(*, name, status="ok", duration_ms=None, server=None, call_id=None, error_type=None, agent_name=None, conversation_id=None, caller=None, dimensions=None, span=None, timestamp=None)` | `tool.call` |
| `record_agent_invocation(*, agent_name, latency_ms=None, status="ok", error_type=None, input_tokens=None, output_tokens=None, cached_tokens=None, reasoning_tokens=None, conversation_id=None, caller=None, dimensions=None, span=None, timestamp=None)` | `agent.invocation` |

The module functions run on the process-wide recorder (`get_recorder()`, replaced with `set_recorder(recorder)`); the same methods exist on every `UsageRecorder`. Each returns the record as sent. `trace_id` / `span_id` come from `span` (default: the current span) and are omitted outside a span. Unknown values are omitted, never sent as `null`.

### Classes

| Name | Description |
|---|---|
| `UsageRecorder(transport=None, *, workload=None, framework=None, source="workload", dimensions=None, validate=None)` | Builds (`build(event, ...)`) and sends (`emit(record)`) records. `workload` is a `Workload` or a callable returning one (default: `Workload.from_env(framework=framework)` per record). `dimensions` are added to every record. `validate` checks each record against the shipped schema and logs violations (default: `ESMERALDA_USAGE_VALIDATE`) |
| `Workload(name, team=None, runtime=None, framework=None, cloud=None)` | `Workload.from_env(*, name=None, framework=None)` reads the environment variables below; `runtime` is detected (Agent Runtime, Cloud Run, GKE, else `external`) |
| `Caller(principal=None, team=None, verified=False, declared={})` | `Caller.from_declared(mapping)` builds an unverified caller from declared context |
| `StdoutTransport(stream=None)` | One JSON line per record on stdout (Cloud Logging `jsonPayload`). The default |
| `OTelLogsTransport(logger_provider=None)` | One OpenTelemetry log record per record (body = the record, event name `esmeralda.usage`) |
| `MultiTransport(*transports)` | Sends to several transports; one failing does not stop the others |
| `UsageSpanProcessor(recorder=None, *, framework=None)` | OpenTelemetry `SpanProcessor` that turns finished GenAI spans into records: `generate_content` / `chat` / `text_completion` → `llm.call`, `execute_tool` → `tool.call`, `invoke_agent` → `agent.invocation`. `caller.*` attributes become `caller.declared`, `esmeralda.dimension.<key>` attributes become `dimensions`. Don't combine it with `EsmeraldaTelemetryPlugin` in one process |

### Functions

| Function | Description |
|---|---|
| `transport_from_env(stream=None)` | Transport chosen by `ESMERALDA_USAGE_TRANSPORT` |
| `usage_from_span(span, recorder=None)` | The record for one finished span (not sent), or `None` for non-GenAI spans |
| `redact(value)` | Drops credential-like keys and masks token-like values (bearer tokens, OAuth access tokens, JWTs, API keys). Applied to every record and legacy event |
| `is_secret_key(key)` | Whether `redact` drops a key; includes `user_auth_token` and the key named by `USER_AUTH_ID` |
| `string_map(values)` | Normalizes `dimensions` / `caller.declared` (snake_case keys, string values, at most 32 entries of 256 characters) |
| `schema_text()` / `schema()` | The contract shipped in the package (`esmeralda/contracts/usage-event.schema.json`, a copy of `contracts/` kept identical by a test), as text / parsed |
| `validate(record)` | List of contract violations (empty when valid). Needs `jsonschema` |

Constants: `SCHEMA = "esmeralda.usage"`, `SCHEMA_VERSION = 1`, `EVENTS`, `RUNTIMES`, `CLOUDS`.

## `esmeralda.context`

Caller context, for telemetry only.

### `class CallerContext(project_id: str, agent_name: str)`

A frozen dataclass.

| Member | Description |
|---|---|
| `CallerContext.from_mapping(value)` | Builds one from untrusted input. Returns `None` unless `value` is a mapping with a non-empty string `project_id` or `agent_name`. Values are trimmed and cut to 128 characters; a missing field becomes `unknown` |
| `to_dict()` | `{"project_id": ..., "agent_name": ...}` |

### Functions

| Function | Description |
|---|---|
| `current_identity() -> CallerContext` | This agent's identity, from `GOOGLE_CLOUD_PROJECT` and `AGENT_NAME` |
| `outgoing_metadata() -> dict` | `{"caller_context": current_identity().to_dict()}`, for A2A request metadata |
| `user_token(ctx)` | The end user's token for the invocation, or `None`. `ctx` is an invocation context or a callback, tool or readonly context. Reads `temp:<USER_AUTH_ID>` session state first, then `run_config.custom_metadata["a2a_metadata"]["user_auth_token"]`. A secret: forward it only through `esmeralda.user_credential`, never log it |
| `user_token_state_key()` | `temp:<USER_AUTH_ID>` (default `temp:user_auth_token`) |
| `from_invocation(invocation_context)` | Returns a `CallerContext`, or `None`. Reads the session state key `temp:caller_context` first, then `run_config.custom_metadata["a2a_metadata"]["caller_context"]` |
| `attach_baggage(caller) -> token` | Sets the baggage entries `caller.project_id` and `caller.agent_name` in the current context |
| `detach_baggage(token) -> None` | Restores the previous context. Skipped if the run was closed from another asyncio task |

### Constants

| Constant | Value |
|---|---|
| `CALLER_CONTEXT_KEY` | `caller_context` |
| `STATE_KEY` | `temp:caller_context` |
| `A2A_METADATA_KEY` | `a2a_metadata` |
| `BAGGAGE_PROJECT_ID` | `caller.project_id` |
| `BAGGAGE_AGENT_NAME` | `caller.agent_name` |

`USER_TOKEN_KEY` = `user_auth_token`: the A2A metadata key, and the default authorization id.

## `esmeralda.auth`

| Name | Description |
|---|---|
| `id_token(audience) -> str` | A Google-signed ID token for `audience`. With `SERVICE_ACCOUNT_EMAIL`, minted through impersonation of that service account (falls back to the runtime identity, with a warning, if impersonation fails); otherwise for the runtime identity. Cached per audience and service account; refreshed before expiry. Raises if no token can be minted |
| `id_token_async(audience)` | `id_token` in a worker thread |
| `access_token() -> str` | An OAuth2 access token (scope `cloud-platform`), for `SERVICE_ACCOUNT_EMAIL` through impersonation when set (same fallback as `id_token`). Cached; refreshed before expiry |
| `access_token_async()` | `access_token` in a worker thread |
| `token_kind(url) -> "id" \| "access"` | `"access"` for hosts listed in `ACCESS_TOKEN_HOSTS` and for `*.googleapis.com`; `"id"` otherwise |
| `bearer_token(url, kind=None) -> str` | `access_token()` or `id_token(audience_for(url))`; `kind` (`"id"` or `"access"`) overrides `token_kind(url)` |
| `audience_for(url)` | The URL's origin, `scheme://host[:port]` |
| `is_local(url)` | `True` for `localhost`, `127.0.0.1`, `::1` |
| `clear_cache()` | Drops cached credentials (tests) |

## `esmeralda.mcp`

| Name | Description |
|---|---|
| `toolset(url, *, prefix, api_key=None, auth_provider=None, timeout=30.0, sse_read_timeout=300.0, token=None, **kwargs)` | An ADK `McpToolset` (streamable HTTP) for the server at `url`. Tool names get `<prefix>_`. `token` (`"id"` or `"access"`) overrides `auth.token_kind(url)`. Extra `kwargs` go to `McpToolset` (for example `tool_filter`) |
| `headers_for(url, ctx=None, *, api_key=None, token=None, auth_provider=None)` | The headers of one request: `Accept`, `Content-Type`, `X-API-Key` (`api_key`, else `AGENT_NAME`), `Authorization: Bearer <token>` (kind from `token`, else `auth.token_kind(url)`; not for local URLs; skipped with a logged error if no token can be minted), the user credential header (`user_credential.outgoing_headers`: only when `url`'s host is listed in `ESMERALDA_USER_CREDENTIAL_TARGETS`, in its configured header, default `X-User-Token`, with the value from `auth_manager.resolve_user_token(ctx, auth_provider=...)`), and `X-Agent-Spiffe-Id` (when `auth_manager.spiffe_id()` is present) |

## `esmeralda.auth_manager`

| Name | Description |
|---|---|
| `DelegatedContext(user_identity=None, agent_spiffe_id=None, auth_provider=None, scopes=())` | Frozen dataclass representing the Three-Identity Delegation tuple (`User identity + Agent SPIFFE identity = Delegated identity`) |
| `spiffe_id() -> str \| None` | Returns the agent's runtime SPIFFE / `principal://` ID from `AGENT_SPIFFE_ID` or `AGENT_WORKLOAD_CERT_PATH` |
| `delegated_context(ctx=None, *, auth_provider=None, scopes=()) -> DelegatedContext` | Builds a `DelegatedContext` for an invocation |
| `acquire_token(auth_provider, *, user_id=None, session_id=None, scopes=(), base_url=None, timeout=10.0) -> str` | Acquires a 3LO/2LO token or API key from Agent Identity Auth Manager (`{auth_provider}:acquireToken`) |
| `resolve_user_token(ctx=None, *, auth_provider=None, scopes=()) -> str \| None` | When `auth_provider` or `AGENT_AUTH_PROVIDER` is configured, a delegated token via `acquire_token` (preferred; a failure raises, no fallback to the raw token); otherwise `context.user_token(ctx)` |

## `esmeralda.user_credential`

| Name | Description |
|---|---|
| `Targets.parse(raw) -> Targets` | Parses the `ESMERALDA_USER_CREDENTIAL_TARGETS` JSON (`{"<host>[:<port>]": "<header>" \| null \| true \| false}`). Invalid input, wildcards and reserved header names are dropped (fail closed) |
| `targets() -> Targets` | The targets from `ESMERALDA_USER_CREDENTIAL_TARGETS` (re-read when it changes) |
| `header_for(url) -> str \| None` / `accepts(url) -> bool` | The header `url`'s host reads the credential from, or `None` when the host isn't listed |
| `outgoing_headers(url, ctx=None, *, auth_provider=None) -> dict` | `{header: credential}` for a listed host, else `{}`. Failures are logged without the value |
| `redact(value, secrets=()) -> value` | A copy with each secret string, and the values of sensitive keys (`user_auth_token`, `temp:<USER_AUTH_ID>`, credential headers, `Authorization`, ...), replaced by `[REDACTED]` |
| `describe_error(exc, secrets=()) -> str` | `"<Type>: <message>"` with secrets masked |
| `DEFAULT_HEADER` | `X-User-Token` |

## `esmeralda.mcp_authz`

| Name | Description |
|---|---|
| `verify_request(headers=None, *, enforce=None, allowed_domains=None) -> VerifiedCaller` | Verifies the user credential header (`user_token_headers()`) and `X-Agent-Spiffe-Id` without network I/O; rejects `*.gserviceaccount.com` tokens and fails closed when `MCP_ENFORCE_USER_AUTH=true` |
| `user_token_headers() -> list[str]` | The headers read, in order: `USER_CREDENTIAL_HEADER` (else `X-User-Token`), `X-User-Token`, the legacy `User-Auth-Token` |
| `decode_jwt_claims(token) -> dict \| None` | Decodes the JSON payload of a 3-part JWT locally |
| `is_service_account_email(value) -> bool` | `True` when `value` is a `*.gserviceaccount.com` service account identifier |
| `AuthorizationError(reason, *, status_code=403)` | Raised when verification fails |

## `esmeralda.attribution`

The catalog caller guard: it maps the platform-verified caller to a catalog caller and team, and rejects (`deny`) or flags callers missing from the catalog. Standard library only; the MCP servers vendor it unchanged as `caller_guard.py`. See [5.3 §5](../5-multi-cloud/03-unified-observability-and-chargeback.md#5-verified-attribution-on-internal-hops-end-to-end).

| Name | Description |
|---|---|
| `CallerGuard(app, allowlist=None, *, exempt_paths=("/health", "/healthz"))` | ASGI middleware. `allowlist` defaults to `Allowlist.from_env()`; without one it passes everything through. Denied requests get `401` (no verified identity) or `403` (unmapped caller, declared mismatch) with a JSON reason. Allowed requests carry the decision in `scope["state"]["esmeralda.caller"]` and `current()`, plus `X-Esmeralda-Billed-Team` when attributed |
| `Allowlist.from_env()` / `from_json(text)` / `from_mapping(data)` | The compiled allowlist (`{"policy": {...}, "token_validated_by": "cloud_run_iam", "callers": [{principal, caller, team, cost_centers, shared}]}`); invalid values raise |
| `verified_principal(headers, *, validated_by=None) -> str \| None` | The caller from the Google ID token in `X-Serverless-Authorization` or `Authorization`: `email`, else `sub` (`spiffe://` ids in their `principal://` form). With `validated_by="cloud_run_iam"` (Cloud Run IAM validated the header) the token counts as is; otherwise only a platform signature-stripped token counts. Malformed or missing tokens give `None` |
| `identity_evidence(headers) -> dict` | The shape of each identity header (`absent`, `opaque`, `jwt_signed`, `jwt_stripped`, `malformed`), never its value; logged with unverified decisions |
| `decide(allowlist, principal, declared_cost_center=None) -> Decision` | Applies `unmapped_caller` and `declared_mismatch` |
| `Decision` | `allowed`, `status`, `reason` (`mapped`, `unmapped`, `unverified`, `declared_mismatch`), `principal`, `caller`, `team`, `cost_center`, `declared_cost_center`, `declared_mismatch`; `verified` is true only for a mapped principal with a team; `caller_record()` is the usage record's `caller` |
| `current() -> Decision \| None` | The decision for the request being served (the plugin uses it to fill `caller.verified`, `caller.principal`, `caller.team`) |
| `ALLOWLIST_ENV`, `DECLARED_COST_CENTER_HEADER`, `BILLED_TEAM_HEADER` | `ESMERALDA_CALLER_ALLOWLIST`, `X-Esmeralda-Cost-Center`, `X-Esmeralda-Billed-Team` |

## `esmeralda.a2a`

| Name | Description |
|---|---|
| `remote_agent(name, *, url, description="", api_key=None, timeout=60.0, token=None)` | An ADK `RemoteA2aAgent` for the agent at `url`. Card from `<url>/v1/card`, with every RPC address pinned to `url` before ADK's checks. Every request gets a bearer token for `url` (kind from `token`, else `auth.token_kind(url)`; not for local URLs) and `X-API-Key` |
| `request_metadata(invocation_context, a2a_message=None, *, url=None)` | The A2A request metadata: `caller_context`, plus `user_auth_token` when the invocation has one and `url`'s host is listed in `ESMERALDA_USER_CREDENTIAL_TARGETS` (never without `url`) |
| `request_metadata_for(url)` | `request_metadata` bound to `url`; usable as `a2a_request_meta_provider` (`remote_agent` uses it) |
| `request_converter(request, part_converter=None)` | ADK's A2A request converter, with `user_auth_token` moved from the request metadata (which ADK copies into every stored event) into `temp:<USER_AUTH_ID>` state. `esmeralda serve` uses it (`A2aAgentExecutorConfig(request_converter=...)`) |

## `esmeralda.a2a_retry`

Timeout and retries of `a2a.remote_agent`. Its `timeout` argument now defaults to `None`, meaning `$A2A_TIMEOUT_SECONDS`, else 300 s.

An A2A `message:send` is not idempotent (a re-send runs the remote agent again), so `remote_agent` only re-sends failures that happen before the callee starts work. At most 3 attempts, with jittered exponential backoff (about 0.5 s, then 1 s; a `Retry-After` up to 10 s is honored).

| Failure | Retried |
|---|---|
| Connect error (refused, DNS, TLS handshake), connect or pool timeout | Yes |
| `429` | Yes |
| `502` / `503` without an A2A task in the body | Yes |
| `502` / `503` whose body carries a task | No |
| `504`, read timeout | **No**: the callee keeps running, so the error is surfaced |
| Any other status or error | No |

| Name | Description |
|---|---|
| `client_timeout(timeout=None) -> float` | `timeout` if given, else `$A2A_TIMEOUT_SECONDS`, else 300 |
| `should_retry(attempt, *, status=None, error=None, task_created=False) -> bool` | The decision table above, for 1-based `attempt` |
| `RetryTransport(inner=None, *, sleep=asyncio.sleep)` | `httpx.AsyncBaseTransport` applying it |

## `esmeralda.telemetry`

### `class TelemetryEmitter(stream=None, *, transport=None, recorder=None)`

Backwards-compatible facade over `esmeralda.usage`. `transport` defaults to `usage.StdoutTransport(stream)` when `stream` is given, else `usage.transport_from_env()`. `emitter.usage` is a `usage.UsageRecorder` on the same transport (or `recorder`). Every event goes through `usage.redact`.

| Member | Event |
|---|---|
| `usage` | `esmeralda.usage` records (`record_llm_call`, `record_tool_call`, `record_agent_invocation`) |
| `transport` | The shared transport |
| `emit(payload)` | Any payload (redacted) |
| `token_consumption(*, agent_id, session_id, user_id, model, prompt_tokens, completion_tokens, total_tokens, thoughts_tokens=0, cached_tokens=0, finish_reason="STOP", execution_path=None, turn_index=1)` | Legacy `genai_token_consumption` (deprecated) |
| `tool_execution(*, agent_id, tool_name, session_id, user_id, status, duration_ms, error_reason=None)` | Legacy `mcp_tool_execution` (deprecated) |

### `class BaggageSpanProcessor(opentelemetry.sdk.trace.SpanProcessor)`

When a span starts, copies the baggage entries `caller.project_id` and `caller.agent_name` onto it as attributes.

### Legacy event schemas

The current Layer 4 log sinks, log-based metrics and dashboards key on `jsonPayload.event`, so the plugin keeps writing these events (`ESMERALDA_LEGACY_TELEMETRY_EVENTS`, on by default) next to the `esmeralda.usage` records. New consumers select `jsonPayload.schema = "esmeralda.usage"` instead.

`genai_token_consumption`:

```json
{
  "event": "genai_token_consumption",
  "session_id": "...", "user_id": "...", "agent_id": "cx_mortgage_orchestrator",
  "execution_path": "cx_mortgage_orchestrator@1", "turn_index": 1,
  "trace_id": "<32 hex>", "span_id": "<16 hex>",
  "model": "gemini-...",
  "tokens": {"prompt_tokens": 100, "completion_tokens": 50, "thoughts_tokens": 20, "cached_tokens": 40, "total_tokens": 170},
  "implicit_caching": {"cache_hit": true, "cache_hit_ratio": 0.4},
  "finish_reason": "STOP"
}
```

`mcp_tool_execution`:

```json
{
  "event": "mcp_tool_execution",
  "session_id": "...", "user_id": "...", "agent_id": "ai_coe_mortgage_specialist",
  "tool_name": "search_documents", "status": "SUCCESS", "duration_ms": 182.4,
  "trace_id": "<32 hex>", "span_id": "<16 hex>",
  "error_reason": "TimeoutError: ..."
}
```

`error_reason` is present only when `status` is `ERROR`. `trace_id` and `span_id` are `unknown_trace` / `unknown_span` outside a span.

## `esmeralda.certs`

Agent Gateway trust.

| Name | Description |
|---|---|
| `parse_bundle(raw) -> list[str]` | The PEM certificates in `raw`. Literal `\n` sequences are accepted |
| `install_gateway_ca(environ=os.environ, *, system_dir=SYSTEM_CA_DIR, certifi_path=None, update_command=UPDATE_COMMAND) -> int` | Writes each certificate to `system_dir/agw-<n>.crt` and runs `update-ca-certificates`, if both exist (otherwise logs a warning). Appends missing certificates to the certifi bundle. Returns the number of certificates; `0` (with a warning) if the variable is unset. Raises `ValueError` if it is set but holds no certificate. Safe to run twice |
| `ENV_VAR` | `AGENT_GATEWAY_ROOT_CERTIFICATES` |
| `SYSTEM_CA_DIR` | `/usr/local/share/ca-certificates` |
| `UPDATE_COMMAND` | `("update-ca-certificates",)` |

## `esmeralda` command line (`esmeralda.cli`)

```text
esmeralda run -- <command> [args...]
```

1. Runs `install_gateway_ca()` and prints `✅ Installed N Agent Gateway root certificate(s) from AGENT_GATEWAY_ROOT_CERTIFICATES`.
2. Replaces itself with `<command>` (`exec`), so the server keeps PID 1 and receives signals directly.

Exit codes (when it doesn't `exec`): `2` for a usage error, `1` if the certificate bundle is invalid. Other failures (for example `update-ca-certificates` failing) stop the container with a traceback.

```text
esmeralda query [--agent-dir DIR] [--url URL | --engine RESOURCE] [--user ID] [--session ID]
                [--caller PROJECT/AGENT] [--timeout SECONDS] [--fail-on-tool-error] [--verbose] MESSAGE
```

| Option | Default | Description |
|---|---|---|
| `--agent-dir` | `.` | Agent directory, with `agent.yaml` and the `agent` package |
| `--url` | | A running server. ADK: `POST <url>/api/stream_reasoning_engine`. A2A: card at `<url>/v1/card` (or `/.well-known/agent-card.json`), then `message:send` |
| `--engine` | | `projects/P/locations/L/reasoningEngines/ID`. ADK: `:streamQuery`. A2A: `<engine>/a2a`. Authenticated with ADC, else `gcloud auth print-access-token` |
| `--user` | `esmeralda-cli` | User id |
| `--session` | new session | Session id (ADK) or context id (A2A) to continue |
| `--caller` | `<project>/esmeralda_cli` | Caller context. The project comes from `GOOGLE_CLOUD_PROJECT`, else ADC |
| `--timeout` | `300` | HTTP timeout, in seconds |
| `--fail-on-tool-error` | off | Also fail when a tool call returned an error |
| `--verbose` | off | Print raw events and tracebacks |

Without `--url` or `--engine`, the agent runs in-process with `env` + `local_env` applied. Exit codes: `0` passed, `1` failed run, `2` usage or configuration error.

```text
esmeralda serve [--agent-dir DIR] [--host HOST] [--port PORT] [--local] [--web] [--otel-to-cloud]
```

| Option | Default | Description |
|---|---|---|
| `--agent-dir` | `.` | Agent directory, with `agent.yaml` and the `agent` package |
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `$PORT`, else `8080` | Port |
| `--local` | off | Apply `local_env` (workstation runs). Without it, only `env` is applied, as defaults |
| `--web` | off | ADK dev UI (`adk web`) instead of the API server. ADK agents only |
| `--otel-to-cloud` | off | Export traces and logs to Google Cloud (`--otel_to_cloud` for ADK; the GCP exporters for A2A) |

ADK agents: replaces itself with `adk api_server <dir> --host --port --no-reload --gemini_enterprise_app_name agent [--otel_to_cloud]`. A2A agents: runs uvicorn with `esmeralda.serve.a2a_server(config)`. Exit code `2` for a usage or configuration error.

```text
esmeralda mcp [--repo PATH]
```

Read-only MCP server on stdio (`fastmcp`). The repository root is `--repo`, else `ESMERALDA_REPO`, else the checkout containing the working directory (the first parent with `infrastructure/live/root.hcl`). Exit code `2` when there is no checkout or `fastmcp` is not installed. Tools: see [Operating a checkout from an agent](guide.md#operating-a-checkout-from-an-agent-esmeralda-mcp).

### Operational CLI subcommands

Every operational subcommand accepts `--repo PATH` (defaults to `$ESMERALDA_REPO` or the git checkout root) and `--json` for machine-readable JSON output:

```text
esmeralda status [--env dev|prd] [--unit UNIT] [--json] [--repo PATH]
esmeralda doctor [--env dev|prd] [--json] [--repo PATH]
esmeralda units [--env dev|prd] [--layer 0..5] [--json] [--repo PATH]
esmeralda plan [<unit> | --all] [--env dev|prd] [--json] [--repo PATH]
esmeralda apply <unit> [--env dev|prd] [--allow-destroy] [--json] [--repo PATH]
esmeralda verify [--env dev|prd] [--probe NAME] [--skip-query] [--partner] [--model-armor] [--json] [--repo PATH]
esmeralda logs <target> [--env dev|prd] [--minutes 30] [--severity DEFAULT] [--limit 50] [--json] [--repo PATH]
esmeralda contracts [--section all|entrypoint|gateway_adapter|agent_card|env_yaml] [--json] [--repo PATH]
esmeralda guard [--staged | --diff RANGE | --all | --message-file FILE] [--json] [--repo PATH]
```

| Subcommand | Backed by | Description |
|---|---|---|
| `status` | `esmeralda.ops.repo.status` / `infra.status` | Git branch, HEAD SHA, dirty/clean state, active worktrees, local state units, image tags/digests vs HEAD, and deployed environment outputs |
| `doctor` | `esmeralda.ops.repo.doctor` | Preflight check: required tools (`git`, `uv`, `terraform`, `terragrunt`, `gcloud`), `ESMERALDA_BILLING_ACCOUNT` (warns if unset), ADC token, `uv.lock` frozen check, and local state readability. Exits non-zero if required checks fail |
| `units` | `esmeralda.ops.infra.list_units` | Lists Terragrunt units for `<env>`, their layer (`0..5`), dependencies, whether enabled/excluded in `env.yaml`, and whether local state exists |
| `plan` | `esmeralda.ops.infra.plan_unit` | Runs read-only `terragrunt plan -lock=false` on a unit (full path or short name such as `entrypoint`) or `--all` enabled units, and prints the resource change summary (`to add`, `to change`, `to destroy`, `to replace`) |
| `apply` | `esmeralda.ops.infra.apply_unit` | Plans `<unit>` to a planfile first, inspects the plan for `destroy` or `replace` actions, **refuses to apply with exit code `2` if `destroy > 0` unless `--allow-destroy` is passed**, and otherwise applies the saved planfile |
| `verify` | `esmeralda.ops.runtime.verify` | Runs gateway conformance probes (`runtime.test_gateway`, optionally filtered by `--probe`) and, unless `--skip-query` is passed, runs remote queries against `ai-coe-mortgage-specialist` and `cx-mortgage-orchestrator` (or with `--partner`, runs Apigee partner conformance checks via `runtime.test_partner`; with `--model-armor`, runs `esmeralda.ops.model_armor.verify_model_armor`). Exits non-zero if any check fails |
| `logs` | `esmeralda.ops.logs.agent_logs` | Pulls recent Cloud Logging entries for an agent (Reasoning Engine) or MCP/gateway service (Cloud Run), collapsing tracebacks and deduplicating repeats |
| `contracts` | `esmeralda.ops.contracts.describe_contracts` | Prints architectural contracts (`all`, `entrypoint`, `gateway_adapter`, `agent_card`, `env_yaml`) |
| `guard` | `esmeralda.ops.guard.run_guard` | Public-repository sensitive-content scanner across `--staged` changes, `--diff <range>`, `--all` tracked files, or `--message-file`. Exits non-zero if any secret, forbidden path, or internal/customer marker is detected |

## `esmeralda.ops`

The functions behind the operational CLI subcommands and `esmeralda mcp`, usable from scripts. Each returns a small JSON-serialisable dict and raises `esmeralda.ops.OpsError` with an actionable message.

| Name | Description |
|---|---|
| `repo.repo_root(start=None)` | `ESMERALDA_REPO`, else the nearest checkout at or above `start` (default: cwd) |
| `repo.status(root, env="dev")` / `repo.doctor(root, env="dev")` | Combined git/worktree/image/infra status; workstation preflight checks |
| `repo.units(root, env)` / `repo.unit_state(root, env, unit)` | Unit paths of an env; a unit's non-sensitive outputs and managed resource count (local state, else `terragrunt output -json`) |
| `infra.status(root, env, unit=None)` / `infra.list_units(root, env, layer=None)` | Environment/unit outputs; unit catalog with layer, dependencies, `enabled`/`excluded`, and `local_state` |
| `infra.plan_unit(root, env, unit)` / `infra.summarize_plan(plan_json)` | Read-only plan of a unit (or `--all`); the summary of a `terraform show -json` plan document |
| `infra.apply_unit(root, env, unit, allow_destroy=False)` | Plan-first apply of a unit with the destroy guard (returns `refused=True, exit_code=2` when `destroy > 0` without `allow_destroy`) |
| `runtime.query_agent(root, env, agent, message)` (async) / `runtime.test_gateway(root, env, probe=None)` / `runtime.verify(root, env, probe=None, skip_query=False)` (async) | Remote query through `esmeralda.query.run`; `scripts/test_gateway.sh` parsed per host; end-to-end verification |
| `logs.agent_logs(root, env, agent, since=, minutes=, severity=, limit=)` / `logs.reduce_entries(entries, limit)` | Reasoning Engine or Cloud Run logs; the collapse / dedupe step on `{time, severity, payload}` entries |
| `contracts.describe_contracts(root, section_name="all")` | Architectural contract sections (`entrypoint`, `gateway_adapter`, `agent_card`, `env_yaml`) and each env's `gateways` block |
| `guard.run_guard(root, mode="staged", diff_range=None, message_file=None)` | Public-repository sensitive-content and forbidden-path scanner |
| `server.build_server(root=None)` | The `FastMCP` server (tests use it with the in-memory `fastmcp.Client`) |

## `esmeralda.serve`

| Name | Description |
|---|---|
| `serve(config, *, host="0.0.0.0", port=None, local=False, web=False, otel_to_cloud=False)` | Serves the agent, as the CLI does |
| `adk_command(config, *, host, port, web=False, otel_to_cloud=False)` | The `adk` command line for an ADK agent |
| `a2a_server(config, *, otel_to_cloud=False)` | The FastAPI app for an A2A agent: the package's `app` behind the A2A REST transport, mounted at `/`, `/a2a` and `/api/a2a`. Uses the package's `a2a_task_store_builder` if it has one, else `InMemoryTaskStore` (warns if `resources.max_instances > 1` without a custom builder) |
| `agent_card(config)` | The A2A `AgentCard` from `agent.yaml` `agent_card`. `A2A_AGENT_URL` overrides `url`; missing fields get defaults |
| `session_service()` | `VertexAiSessionService` when `GOOGLE_CLOUD_AGENT_ENGINE_ID` is set and `USE_IN_MEMORY_SESSIONS` isn't `1`; else `InMemorySessionService` |
| `install_caller_guard(server) -> bool` | Adds `attribution.CallerGuard` when `ESMERALDA_CALLER_ALLOWLIST` is set, except on Agent Runtime (the container never sees the caller's credential; the Agent Gateway record attributes those calls). Called by `a2a_server` |

## `esmeralda.config`

### `class AgentConfig`

`AgentConfig.load(directory=".")` reads `<directory>/agent.yaml`.

| Field | From `agent.yaml` | Description |
|---|---|---|
| `name` | `name` | Deployment name (defaults to the directory name) |
| `framework` | `framework` | `google-adk` (default) or `a2a`; anything else raises `ValueError` |
| `env` | `env` | Environment of the deployed agent (values as strings; booleans as `true`/`false`) |
| `local_env` | `local_env` | Overrides for local runs only |
| `agent_card` | `agent_card` | A2A agent card |

`apply_local_env(environ=os.environ)` sets `env`, then `local_env`, without overriding variables already set.

## `esmeralda.query` and `esmeralda.report`

| Name | Description |
|---|---|
| `Query(message, user_id="esmeralda-cli", session_id=None, caller=None, timeout=300.0)` | One query |
| `run(config, query, report, *, url=None, engine=None)` | Runs the query on the chosen target and feeds the report |
| `load_package(config)` | Imports the agent package (`agent`) from the agent directory |
| `load_app(config)` | The package's `app` (or its `root_agent` wrapped with `create_app`) |
| `parse_caller("project/agent")` | A `CallerContext` |
| `Report(stream=sys.stdout, verbose=False)` | Renders ADK events (`adk_event`) and A2A tasks (`a2a_task`); `summary(fail_on_tool_error=False)` prints the result and returns the exit code. `answer`, `authors`, `tool_names`, `tool_errors`, `tokens` and `failures()` hold the result for programmatic use |

## Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `AGENT_GATEWAY_ROOT_CERTIFICATES` | `esmeralda run` | PEM bundle to trust. Injected by Terraform (Layer 4 output `agw_root_ca_bundle`) |
| `AGENT_NAME` | Plugin, `current_identity`, `usage.Workload.from_env` | Agent name in telemetry and in outgoing caller context |
| `GOOGLE_CLOUD_PROJECT` | `default_environment`, `genai_client_defaults`, `current_identity` | Set from ADC if missing |
| `GOOGLE_CLOUD_LOCATION` | `default_environment` | Defaulted to `global` if missing |
| `GOOGLE_GENAI_USE_VERTEXAI` | `default_environment` | Defaulted to `True` if missing |
| `GRPC_DNS_RESOLVER` | `default_environment` | Defaulted to `native` if missing |
| `MODEL_LOCATION` | `genai_client_defaults` | Location for every genai client (for example `global`) |
| `MODEL_NAME` | Plugin | Fallback model in token events and usage records, when neither the request nor the response names one |
| `*_PROXY` | `log_egress_proxy` | Logged at startup |
| `SERVICE_ACCOUNT_EMAIL` | `auth.id_token`, `auth.access_token` | Service account to impersonate for ID and access tokens (set by Terraform) |
| `ACCESS_TOKEN_HOSTS` | `auth.token_kind` | Comma-separated hostnames that take an access token instead of an ID token (agent hosts served by `direct`; set by Terraform from the service catalog) |
| `ESMERALDA_REPO` | `esmeralda mcp`, `esmeralda.ops` | Repository root (default: the checkout containing the working directory) |
| `USER_AUTH_ID` | `context.user_token`, `usage.redact` | Gemini Enterprise authorization id of the user token (default `user_auth_token`); that key is never emitted in telemetry |
| `ESMERALDA_USER_CREDENTIAL_TARGETS` | `user_credential`, `mcp.headers_for`, `a2a.request_metadata` | JSON `{"<host>": "<header>"}`: the only hosts that receive the delegated user credential, and the header each reads (default `X-User-Token`). Unset or malformed: none |
| `USER_CREDENTIAL_HEADER` | `mcp_authz.verify_request` (MCP servers) | Header the server reads the user credential from (default `X-User-Token`) |
| `ESMERALDA_CALLER_ALLOWLIST` | `attribution.CallerGuard`, `serve.install_caller_guard` | JSON allowlist compiled from the catalog (`attribution` in `catalog/.resolved/<env>.json`, written by the Terraform unit). Unset: guard off. Invalid: startup error |
| `ESMERALDA_WORKLOAD_NAME` | `usage.Workload.from_env` | `workload.name` of usage records (default: the plugin's agent name, else `AGENT_NAME`, `OTEL_SERVICE_NAME`, `K_SERVICE`) |
| `ESMERALDA_TEAM` | `usage.Workload.from_env` | `workload.team` |
| `ESMERALDA_RUNTIME` | `usage.Workload.from_env` | `workload.runtime` (`agent_engine`, `cloud_run`, `gke`, `external`); detected from `GOOGLE_CLOUD_AGENT_ENGINE_ID`, `K_SERVICE`, `KUBERNETES_SERVICE_HOST` if unset |
| `ESMERALDA_FRAMEWORK` | `usage.Workload.from_env` | `workload.framework` (default set by the producer: `adk` for the plugin) |
| `ESMERALDA_CLOUD` | `usage.Workload.from_env` | `workload.cloud` (default `gcp`) |
| `ESMERALDA_USAGE_TRANSPORT` | `usage.transport_from_env` | `stdout` (default), `otel`, or `stdout,otel` |
| `ESMERALDA_USAGE_VALIDATE` | `usage.UsageRecorder` | `true` validates every record against the shipped schema and logs violations (needs `jsonschema`) |
| `ESMERALDA_LEGACY_TELEMETRY_EVENTS` | Plugin | `false` stops the legacy `genai_token_consumption` / `mcp_tool_execution` events (default on) |
