# Usage guide

This page shows how to use the `esmeralda` library in each situation. For the full API, see the [package reference](reference.md).

## Which piece do I need?

| Situation | Use |
|---|---|
| Any agent container on Agent Runtime | `ENTRYPOINT ["esmeralda", "run", "--"]` in the Dockerfile |
| Any agent package | `esmeralda.prepare()` at the top of `agent/__init__.py`, before importing the agent definition |
| Agent served by `adk api_server` or `adk web` (the default) | `app = esmeralda.create_app(root_agent)` in `agent/__init__.py` |
| Agent served by your own runner (for example an A2A server) | `EsmeraldaTelemetryPlugin()` in the runner's `plugins`, and `esmeralda.finalize()` once the tracer provider is set up |
| Agent that calls another agent over A2A | `esmeralda.context.outgoing_metadata()` in the A2A request metadata |
| Client or test calling an agent through the Agent Runtime API | `"state_delta": {"temp:caller_context": {...}}` in the query input |
| Agent-specific plugins (BigQuery analytics, ...) | `esmeralda.create_app(root_agent, plugins=[...])` |
| Agent-specific process setup | `esmeralda.prepare([*esmeralda.lifecycle.PREPARE, my_step])` |

## Add the library to an agent

These steps apply to a new agent and to an existing one. The [CX mortgage orchestrator](../../../apps/agents/cx-mortgage-orchestrator/agent/__init__.py) is the reference.

### 1. Depend on it from the workspace

In the agent's `pyproject.toml`:

```toml
[project]
dependencies = [
    "esmeralda",
    # ...
]

[tool.uv.sources]
esmeralda = { workspace = true }
```

Then regenerate the lock with `make lock` and commit `uv.lock` (see [Contributing: Dependencies](../contributing.md)).

### 2. Prepare the process and export the app

`agent/__init__.py`:

```python
import esmeralda

esmeralda.prepare()

from .agent import root_agent  # noqa: E402

app = esmeralda.create_app(root_agent)
```

Order matters: `prepare()` patches the google-genai and aiohttp clients, so it must run before `agent.py` creates the agent and its clients.

`create_app` returns an ADK `App` named `agent`, with `EsmeraldaTelemetryPlugin` first. The ADK loader picks up `app` before `root_agent`. The name `agent` must match the package directory and the `--gemini_enterprise_app_name` passed to `adk api_server`.

> [!NOTE]
> Don't wire telemetry callbacks on the agent itself (`after_model_callback=...`, `after_tool_callback=...`). The plugin already emits the events for every agent in the app, and doing both would emit them twice.

### 3. Start the container through `esmeralda run`

`Dockerfile`:

```dockerfile
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# The esmeralda wheel, built by `make build-*` into dist/. Its dependencies are in requirements.lock.
COPY dist/ /tmp/dist/
RUN pip install --no-cache-dir --no-deps /tmp/dist/esmeralda-*.whl && rm -rf /tmp/dist

COPY . .

ENV GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

ENTRYPOINT ["esmeralda", "run", "--"]
CMD ["sh", "-c", "exec adk api_server . --host 0.0.0.0 --port ${PORT:-8080} --no-reload --gemini_enterprise_app_name agent --otel_to_cloud"]
```

The base image needs `ca-certificates`, so that `update-ca-certificates` is available.

### 4. Build the wheel into the image

`requirements.lock` is exported with `--no-emit-workspace`, so it doesn't contain `esmeralda`. Pass the library as the fourth argument of `build_image` in the Makefile, and the build will put its wheel in `dist/`:

```makefile
build-my-agent: ## Build and push my agent image
	$(call build_image,apps/agents/my-agent,my-agent,my-agent,esmeralda)
```

## Agents with their own runner (A2A servers)

An A2A server builds its own ADK `Runner`, so `create_app` isn't used. Pass the plugin to the runner, and call `finalize()` after the tracer provider is set up. The [AI CoE mortgage specialist](../../../apps/agents/ai-coe-mortgage-specialist/agent_app.py) does this:

```python
from esmeralda import EsmeraldaTelemetryPlugin
import esmeralda

runner = Runner(agent=my_agent, app_name="agent", session_service=..., plugins=[EsmeraldaTelemetryPlugin(), *other_plugins])


class TelemetryA2aAgent(A2aAgent):
    def set_up(self):
        super().set_up()
        ...  # set up the OpenTelemetry providers
        esmeralda.finalize()
```

The agent package still calls `esmeralda.prepare()` on import. The container still starts through `esmeralda run`.

## Caller context

The caller context says which project and agent made a call. The plugin puts it in OpenTelemetry baggage for the run, and every span created during the run gets the attributes `caller.project_id` and `caller.agent_name`. It is for telemetry and cost attribution, never for authorization.

### Agent-to-agent calls (A2A)

Add `outgoing_metadata()` to the request metadata of the remote agent. The calling agent identifies itself from `GOOGLE_CLOUD_PROJECT` and `AGENT_NAME`:

```python
from esmeralda import context


def a2a_metadata_provider(invocation_context, a2a_message):
    return {**context.outgoing_metadata(), "other_key": "..."}


remote = RemoteA2aAgent(..., a2a_request_meta_provider=a2a_metadata_provider)
```

On the receiving side, ADK exposes the metadata as `run_config.custom_metadata["a2a_metadata"]`, where the plugin reads it.

### Direct calls through the Agent Runtime API

Send the context as temporary session state. The `temp:` prefix keeps it for the current invocation only; it is never written to the session:

```json
{
  "class_method": "async_stream_query",
  "input": {
    "message": "Verify Julian Sterling's income",
    "user_id": "user-1",
    "session_id": "1234",
    "state_delta": {"temp:caller_context": {"project_id": "team-a-project", "agent_name": "team_a_agent"}}
  }
}
```

> [!WARNING]
> Don't pass `caller_context` as a top-level query argument. The runtime forwards unknown arguments to the ADK runner, which rejects them.

## Agent-specific plugins and setup

Extra ADK plugins go after `EsmeraldaTelemetryPlugin`:

```python
app = esmeralda.create_app(root_agent, plugins=[create_bq_plugin()])
```

Extra process setup is a function without arguments, added to the default steps. Make it idempotent (safe to run twice) and let it raise on failure. A half-configured agent should not start.

```python
def enable_my_sdk_proxy() -> None:
    ...

esmeralda.prepare([*esmeralda.lifecycle.PREPARE, enable_my_sdk_proxy])
```

The same applies to `finalize`, with `esmeralda.lifecycle.FINALIZE`. When you pass custom finalize steps, give them to the plugin too: `EsmeraldaTelemetryPlugin(finalize_steps=...)`.

## Running and testing locally

- **`adk web` / `adk api_server`** load `app` exactly as in production. Without `AGENT_GATEWAY_ROOT_CERTIFICATES`, the entrypoint only logs a warning, and you don't need it locally.
- **Scripts** can wrap the app with the Agent Engine template: `AdkApp(app=app)` (see [test_local.py](../../../apps/agents/cx-mortgage-orchestrator/scripts/test_local.py)).
- **Unit tests that mock ADK or google-genai** (as the agents' `conftest.py` do) should stub `prepare`, because it patches the real clients. The library's own tests cover the patches:

  ```python
  import esmeralda

  esmeralda.prepare = lambda *args, **kwargs: None
  ```

- **Run commands through `make`**, or set `UV_FROZEN=1`. A bare `uv run` re-locks `uv.lock` through the corporate package proxy. The pre-push lock check catches it, but it costs a `make lock` to fix.

Library tests:

```bash
make test-agents   # agents and esmeralda
uv run --frozen --package esmeralda --extra dev pytest packages/esmeralda/tests
```

## Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| `CERTIFICATE_VERIFY_FAILED` on egress | Root CA not installed | Agent logs show `✅ Installed N Agent Gateway root certificate(s)`; the engine has `AGENT_GATEWAY_ROOT_CERTIFICATES`; the Dockerfile `ENTRYPOINT` is `esmeralda run` |
| Container exits right away with `contains no PEM certificate` | The env var is set but malformed | The Layer 4 output `agw_root_ca_bundle` |
| `Network is unreachable` from Gemini or sessions | `prepare()` ran after the clients were created | `esmeralda.prepare()` is the first statement in `agent/__init__.py` |
| No `mcp_tool_execution` / `genai_token_consumption` events | The plugin isn't in the runner | The package exports `app` (`create_app`), or the custom runner lists `EsmeraldaTelemetryPlugin()` |
| Events appear twice | Telemetry callbacks also wired on the agent | Remove `after_model_callback` / `after_tool_callback` telemetry from the agent |
| Spans without `caller.*` attributes | The caller didn't send the context, or no SDK tracer provider | `state_delta` / A2A metadata on the caller side; a one-time warning `No OpenTelemetry SDK tracer provider` in the logs |
