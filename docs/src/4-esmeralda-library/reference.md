# Package reference

The reference for `esmeralda` 0.1.0 ([source](../../../packages/esmeralda/src/esmeralda/__init__.py)). For when to use each piece, see the [usage guide](guide.md).

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
EsmeraldaTelemetryPlugin(*, agent_name=None, finalize_steps=lifecycle.FINALIZE, emitter=None)
```

| Parameter | Description |
|---|---|
| `agent_name` | `agent_id` in telemetry events. Defaults to the `AGENT_NAME` environment variable, then `unknown_agent` |
| `finalize_steps` | The steps passed to `finalize()` at the start of each run |
| `emitter` | A `TelemetryEmitter`. Defaults to one that writes to stdout |

Plugin name: `esmeralda_telemetry`. Callbacks:

| Callback | Behavior |
|---|---|
| `before_run_callback` | Runs `finalize`. Reads the caller context (`context.from_invocation`) and, if present, attaches it as baggage for the run |
| `after_run_callback` | Detaches the baggage of that invocation |
| `before_tool_callback` | Records the tool start time |
| `after_tool_callback` | Emits `mcp_tool_execution` with `status=SUCCESS` |
| `on_tool_error_callback` | Emits `mcp_tool_execution` with `status=ERROR` and `error_reason`. Returns `None`, so the agent's own error handling still runs |
| `after_model_callback` | Emits `genai_token_consumption` for complete (non-partial) responses with token counts |

None of the callbacks change the request or the response. Telemetry failures are logged and never break the run. Per-invocation bookkeeping is bounded (1024 entries) for runs that never reach `after_run`.

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
| `user_token(ctx)` | The end user's token for the invocation, or `None`. `ctx` is an invocation context or a callback, tool or readonly context. Reads `temp:<USER_AUTH_ID>` session state first, then `run_config.custom_metadata["a2a_metadata"]["user_auth_token"]` |
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
| `audience_for(url)` | The URL's origin, `scheme://host[:port]` |
| `is_local(url)` | `True` for `localhost`, `127.0.0.1`, `::1` |
| `clear_cache()` | Drops cached credentials (tests) |

## `esmeralda.mcp`

| Name | Description |
|---|---|
| `toolset(url, *, prefix, api_key=None, timeout=30.0, sse_read_timeout=300.0, **kwargs)` | An ADK `McpToolset` (streamable HTTP) for the server at `url`. Tool names get `<prefix>_`. Extra `kwargs` go to `McpToolset` (for example `tool_filter`) |
| `headers_for(url, ctx=None, *, api_key=None)` | The headers of one request: `Accept`, `Content-Type`, `X-API-Key` (`api_key`, else `AGENT_NAME`), `Authorization: Bearer <ID token>` (not for local URLs; skipped with a logged error if no token can be minted), `User-Auth-Token` (when `context.user_token(ctx)` has one) |

## `esmeralda.a2a`

| Name | Description |
|---|---|
| `remote_agent(name, *, url, description="", api_key=None, timeout=60.0)` | An ADK `RemoteA2aAgent` for the agent at `url`. Card from `<url>/v1/card`, with every RPC address pinned to `url` before ADK's checks. Every request gets an ID token for `url`'s origin (not for local URLs) and `X-API-Key` |
| `request_metadata(invocation_context, a2a_message=None)` | The A2A request metadata: `caller_context`, plus `user_auth_token` when the invocation has one. Usable as `a2a_request_meta_provider` |

## `esmeralda.telemetry`

### `class TelemetryEmitter(stream=None)`

Writes each event as one JSON line to `stream` (stdout by default). Agent Runtime ingests the line as the log entry's `jsonPayload`.

| Method | Event |
|---|---|
| `emit(payload)` | Any payload |
| `token_consumption(*, agent_id, session_id, user_id, model, prompt_tokens, completion_tokens, total_tokens, thoughts_tokens=0, cached_tokens=0, finish_reason="STOP", execution_path=None, turn_index=1)` | `genai_token_consumption` |
| `tool_execution(*, agent_id, tool_name, session_id, user_id, status, duration_ms, error_reason=None)` | `mcp_tool_execution` |

### `class BaggageSpanProcessor(opentelemetry.sdk.trace.SpanProcessor)`

When a span starts, copies the baggage entries `caller.project_id` and `caller.agent_name` onto it as attributes.

### Event schemas

The Layer 4 log sinks, log-based metrics and dashboards key on `jsonPayload.event`.

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

## `esmeralda.serve`

| Name | Description |
|---|---|
| `serve(config, *, host="0.0.0.0", port=None, local=False, web=False, otel_to_cloud=False)` | Serves the agent, as the CLI does |
| `adk_command(config, *, host, port, web=False, otel_to_cloud=False)` | The `adk` command line for an ADK agent |
| `a2a_server(config, *, otel_to_cloud=False)` | The FastAPI app for an A2A agent: the package's `app` behind the A2A REST transport, mounted at `/`, `/a2a` and `/api/a2a`. Uses the package's `a2a_task_store_builder` if it has one |
| `agent_card(config)` | The A2A `AgentCard` from `agent.yaml` `agent_card`. `A2A_AGENT_URL` overrides `url`; missing fields get defaults |
| `session_service()` | `VertexAiSessionService` when `GOOGLE_CLOUD_AGENT_ENGINE_ID` is set and `USE_IN_MEMORY_SESSIONS` isn't `1`; else `InMemorySessionService` |

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
| `Report(stream=sys.stdout, verbose=False)` | Renders ADK events (`adk_event`) and A2A tasks (`a2a_task`); `summary(fail_on_tool_error=False)` prints the result and returns the exit code |

## Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `AGENT_GATEWAY_ROOT_CERTIFICATES` | `esmeralda run` | PEM bundle to trust. Injected by Terraform (Layer 4 output `agw_root_ca_bundle`) |
| `AGENT_NAME` | Plugin, `current_identity` | Agent name in telemetry and in outgoing caller context |
| `GOOGLE_CLOUD_PROJECT` | `default_environment`, `genai_client_defaults`, `current_identity` | Set from ADC if missing |
| `GOOGLE_CLOUD_LOCATION` | `default_environment` | Defaulted to `global` if missing |
| `GOOGLE_GENAI_USE_VERTEXAI` | `default_environment` | Defaulted to `True` if missing |
| `GRPC_DNS_RESOLVER` | `default_environment` | Defaulted to `native` if missing |
| `MODEL_LOCATION` | `genai_client_defaults` | Location for every genai client (for example `global`) |
| `MODEL_NAME` | Plugin | Fallback `model` in token events, when the response has no model version |
| `*_PROXY` | `log_egress_proxy` | Logged at startup |
| `SERVICE_ACCOUNT_EMAIL` | `auth.id_token` | Service account to impersonate for ID tokens (set by Terraform) |
| `USER_AUTH_ID` | `context.user_token` | Gemini Enterprise authorization id of the user token (default `user_auth_token`) |
