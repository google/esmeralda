# Usage guide

This page shows how to use the `esmeralda` library in each situation. For the full API, see the [package reference](reference.md).

## Which piece do I need?

| Situation | Use |
|---|---|
| Any agent container on Agent Runtime | `ENTRYPOINT ["esmeralda", "run", "--"]` in the Dockerfile |
| Any agent package | `esmeralda.prepare()` at the top of `agent/__init__.py`, before importing the agent definition |
| Every agent package | `app = esmeralda.create_app(root_agent)` in `agent/__init__.py` |
| Start the agent's server (container or workstation) | `esmeralda serve` / `make serve AGENT=<agent>` |
| Agent served over A2A | `framework: a2a` and `agent_card` in `agent.yaml`; the package exports `app` like any agent, and `esmeralda serve` does the rest |
| A runner you build yourself | `EsmeraldaTelemetryPlugin()` in the runner's `plugins`, and `esmeralda.finalize()` once the tracer provider is set up |
| Agent that calls MCP tool servers through the gateway | `esmeralda.mcp.toolset(url, prefix=...)` |
| Agent that calls another agent over A2A | `esmeralda.a2a.remote_agent(name, url=...)` as a sub-agent |
| Client or test calling an agent through the Agent Runtime API | `"state_delta": {"temp:caller_context": {...}}` in the query input |
| Agent-specific plugins (BigQuery analytics, ...) | `esmeralda.create_app(root_agent, plugins=[...])` |
| Agent-specific process setup | `esmeralda.prepare([*esmeralda.lifecycle.PREPARE, my_step])` |
| Run or test an agent, locally or deployed | `esmeralda query`, or `make query AGENT=<agent>` (add `TARGET=remote` or `TARGET=<url>`) |

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

### 3. Start the container through `esmeralda run` and `esmeralda serve`

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
CMD ["esmeralda", "serve", "--otel-to-cloud"]
```

The same `CMD` works for ADK and A2A agents: `esmeralda serve` reads `framework` from `agent.yaml` (see [Serving an agent](#serving-an-agent)). The base image needs `ca-certificates`, so that `update-ca-certificates` is available.

### 4. Build the wheel into the image

`requirements.lock` is exported with `--no-emit-workspace`, so it doesn't contain `esmeralda`. Pass the library as the fourth argument of `build_image` in the Makefile, and the build will put its wheel in `dist/`:

```makefile
build-my-agent: ## Build and push my agent image
	$(call build_image,apps/agents/my-agent,my-agent,my-agent,esmeralda)
```

## Serving an agent

`esmeralda serve` starts the agent's server, the same way in the container and on a workstation. It reads `framework` from `agent.yaml`:

| Framework | Server |
|---|---|
| `google-adk` | `adk api_server` for the agent directory, with the Agent Runtime endpoints (`--gemini_enterprise_app_name agent`). `--web` starts the ADK dev UI instead |
| `a2a` | An A2A REST server for the package's `app`, with the agent card from `agent.yaml` `agent_card` (`A2A_AGENT_URL` overrides its `url`). Mounted at `/`, `/a2a` and `/api/a2a` |

| Option | Container | Workstation |
|---|---|---|
| `--otel-to-cloud` | Yes: exports traces and logs to Google Cloud | Usually not |
| `--local` | No | Yes: applies `local_env` from `agent.yaml` |
| `--port` | `$PORT` (default 8080) | Any |

Locally, `make serve` adds `--local` and starts the local MCP servers:

```bash
make serve AGENT=ai-coe-mortgage-specialist            # A2A server on :8080
make serve AGENT=cx-mortgage-orchestrator PORT=8081    # ADK API server on :8081
make serve AGENT=cx-mortgage-orchestrator WEB=1        # ADK dev UI
make query AGENT=cx-mortgage-orchestrator TARGET=http://localhost:8081
```

> [!NOTE]
> The first time ADK runs in a terminal, it asks once whether to enable ADK usage telemetry. It never asks in the container, which has no terminal.

### What an A2A agent package provides

An A2A agent is written like any other agent: the package exports `app` (`esmeralda.create_app`). `esmeralda serve` builds the A2A executor, the session service and the task store around it:

| Concern | Default | Override |
|---|---|---|
| Sessions | Vertex AI managed sessions on Agent Runtime (`GOOGLE_CLOUD_AGENT_ENGINE_ID`); in memory locally | `USE_IN_MEMORY_SESSIONS=1` |
| A2A task store | In memory | Export `a2a_task_store_builder` from the package (a callable that returns an A2A `TaskStore`). The [specialist](../../../apps/agents/ai-coe-mortgage-specialist/agent/__init__.py) uses Cloud SQL when `USE_CLOUD_SQL=1` |
| Plugins | `EsmeraldaTelemetryPlugin` | The app's other plugins (for example BigQuery analytics) |

`finalize()` runs once the tracer provider is set up.

## Calling MCP servers and other agents

Calls through the gateway need the same things every time: an ID token for the target, the end user's token when there is one, and the agent's API key, which the gateway uses for rate limiting. The library adds them for you.

### MCP tool servers

```python
import os

from esmeralda import mcp

dms_toolset = mcp.toolset(os.environ.get("DMS_MCP_URL", "https://legacy-dms.esmeralda.internal/mcp"), prefix="dms")

root_agent = Agent(..., tools=[dms_toolset])
```

Each request carries:
- `Authorization: Bearer <ID token>` for the server's origin. Not sent to `localhost`, so local MCP servers work as-is.
- `User-Auth-Token`, when the invocation has a user token (see below).
- `X-API-Key: <AGENT_NAME>`.

### Other agents (A2A)

```python
from esmeralda import a2a

specialist = a2a.remote_agent(
    "mortgage_tools_agent",
    url=os.environ["A2A_AGENT_URL"],  # the callee's gateway address, e.g. https://<agent>.esmeralda.internal
    description="Delegate all mortgage-related queries to this agent.",
)

root_agent = Agent(..., sub_agents=[specialist])
```

The remote agent fetches the card from `<url>/v1/card`, and pins every RPC address on it to `url`, so calls always go through the configured gateway address. ADK's own checks still apply: https, or http on a loopback host. Each request carries an ID token for `url`'s origin and the API key. Each message carries this agent's caller context and the user token in A2A metadata.

### ID tokens

ID tokens come from `esmeralda.auth`. With `SERVICE_ACCOUNT_EMAIL` set, they are minted for that service account through impersonation; otherwise for the runtime identity. Each is cached per audience and refreshed shortly before it expires, so tool calls don't pay for IAM round trips. If impersonation fails, the library logs a warning and falls back to the runtime identity.

### The user's token

When Gemini Enterprise calls an agent with a user authorization, the runtime stores the user's OAuth token as `temp:<authorization id>` session state. It is ephemeral and never written to the session. Set `USER_AUTH_ID` to the authorization id configured in Gemini Enterprise (default `user_auth_token`).

`esmeralda.context.user_token(ctx)` finds the token for the current invocation, either in that state or in the A2A metadata of a calling agent. Toolsets and remote agents forward it on their own. Agent code can call it from a tool or a callback.

## Caller context

The caller context says which project and agent made a call. The plugin puts it in OpenTelemetry baggage for the run, and every span created during the run gets the attributes `caller.project_id` and `caller.agent_name`. It is for telemetry and cost attribution, never for authorization.

### Agent-to-agent calls (A2A)

`esmeralda.a2a.remote_agent` sends it automatically: the calling agent identifies itself from `GOOGLE_CLOUD_PROJECT` and `AGENT_NAME`. A hand-built `RemoteA2aAgent` can pass `a2a_request_meta_provider=esmeralda.a2a.request_metadata`.

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

## Querying an agent: locally, on a server, or deployed

`esmeralda query` calls any agent, ADK or A2A, with one command and one report. It reads `framework` from the agent's `agent.yaml` to choose how to talk to it:

| Target | Command | How it calls the agent |
|---|---|---|
| In-process (default) | `esmeralda query --agent-dir apps/agents/<agent> "message"` | Loads the package's `app` and runs it through the ADK runner. No server or port needed, and it works in a debugger |
| A running server | `... --url http://localhost:8080 "message"` | ADK: `POST /api/stream_reasoning_engine` (`async_stream_query`). A2A: agent card, then `message:send` |
| The deployed agent | `... --engine projects/P/locations/L/reasoningEngines/ID "message"` | The same calls through the Agent Runtime API, with your gcloud credentials |

Every run prints the same report: the events as they arrive (agent transfers, tool calls and results, text), then the answer, tool and token counts, and a verdict. The exit code is `0` on success, and `1` if the agent reported an error, returned no answer, or (A2A) the task didn't complete. Add `--fail-on-tool-error` to also fail when a tool call returned an error.

The caller context is always sent, as `<project>/esmeralda_cli` by default (override with `--caller project/agent`). Other options: `--user`, `--session` (continue a conversation), `--timeout`, `--verbose` (raw events).

From the repository root, `make query` resolves the agent directory and the deployed engine for you:

```bash
make query AGENT=ai-coe-mortgage-specialist                           # in-process, local MCP servers started for you
make query AGENT=cx-mortgage-orchestrator QUERY="Verify Julian Sterling's income"
make query AGENT=cx-mortgage-orchestrator TARGET=remote ENV=dev       # the engine deployed in dev
make query AGENT=cx-mortgage-orchestrator TARGET=http://localhost:8080
make test-e2e ENV=dev                                                 # remote query: specialist, then orchestrator
```

### Local runs and `local_env`

A local run applies the agent's `agent.yaml` `env` (the same variables the deployed engine gets), then its `local_env`, without overriding variables already set in your shell. `local_env` holds what differs on a workstation:

```yaml
local_env:
  LOCAL_MODE: "true"                               # orchestrator: load the specialist in-process
  EMAIL_MCP_URL: "http://localhost:8001/mcp"       # local MCP servers started by make query
  INCOME_VERIFICATION_URL: "http://localhost:8002/mcp"
  DMS_MCP_URL: "http://localhost:8003/mcp"
```

Local runs call Gemini on Vertex AI with your Application Default Credentials. Run `gcloud auth application-default login` with an account that can use Vertex AI, and set `GOOGLE_CLOUD_PROJECT` to a project where it is enabled.

## Running and testing locally

- **`esmeralda query`** (above) is the standard way to run an agent locally.
- **`adk web` / `adk api_server`** load `app` exactly as in production. Without `AGENT_GATEWAY_ROOT_CERTIFICATES`, the entrypoint only logs a warning, and you don't need it locally.
- **Scripts** can wrap the app with the Agent Engine template: `AdkApp(app=app)`.
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
