# packages/esmeralda/AGENTS.md

Local rules for the `esmeralda` runtime library. The root `AGENTS.md` still applies. User docs:
`docs/src/4-esmeralda-library/` (`guide.md`, `reference.md`); keep them in sync with any API change.

## API surface

Every agent image depends on this package, so treat these as public API (change them only with
care, and update both agents and the docs in the same PR):

| Surface | Module | Purpose |
|---|---|---|
| `prepare()`, `finalize()` | `lifecycle.py` | Process lifecycle; `prepare()` must run before the agent creates its clients |
| `create_app(root_agent, *, name="agent", plugins=())` | `app.py` | ADK `App` with `EsmeraldaTelemetryPlugin` first |
| `EsmeraldaTelemetryPlugin` | `plugin.py` | Caller context and telemetry per request |
| `mcp.toolset(url, prefix=...)`, `mcp.headers_for(...)` | `mcp.py` | MCP toolset with ID token, user token and `X-API-Key` |
| `a2a.remote_agent(name, url=..., description=...)` | `a2a.py` | `RemoteA2aAgent` pinned to the gateway URL, card from `<url>/v1/card` |
| `auth.id_token(audience)`, `auth.id_token_async(...)` | `auth.py` | Cached ID tokens; impersonates `SERVICE_ACCOUNT_EMAIL` when set; skipped for local URLs |
| `context.user_token(ctx)`, `context.CallerContext` | `context.py` | End-user token and caller identity propagation |
| `esmeralda run -- <cmd>` / `serve` / `query` | `cli.py` (+ `certs.py`, `serve.py`, `query.py`) | Container entrypoint (installs the Agent Gateway root CA), server from `agent.yaml`, one client for ADK and A2A |
| `esmeralda mcp` / `status` / `doctor` / `units` / `plan` / `apply` / `verify` / `logs` / `contracts` / `guard` | `cli.py` + `ops/` | Read-only MCP server (`mcp`) and first-class platform operations CLI (`--json` + human formatters, destroy guard on `apply`, sensitive content `guard`) |
| `AgentConfig` | `config.py` | Parsed `agent.yaml` (`framework`, `env`, `local_env`, `agent_card`) |

The top-level `esmeralda` namespace exports only `prepare`, `finalize`, `create_app` and
`EsmeraldaTelemetryPlugin`; import the rest from their modules (`from esmeralda import mcp, a2a`).

## Rules

- Keep it generic: no agent-, team- or environment-specific values (project IDs, hostnames other than defaults documented as examples).
- Dependencies are declared in `pyproject.toml`; any change needs `make lock` and a committed `uv.lock`. The wheel is built into each agent image by `make build-<agent>` and installed with `--no-deps`, so every runtime dependency must also be in the agents' locked set.
- Python ≥ 3.12, ruff settings from the root `pyproject.toml` (line length 120).

## Tests

- `make test-agents` runs this package's tests together with the agents'; alone: `uv run --package esmeralda --extra dev pytest packages/esmeralda/tests/`.
- Tests are offline (no GCP calls): mock credentials and HTTP. Add a test in `tests/` for every behaviour change.
- After a change that affects serving, auth or headers, also run `make query AGENT=cx-mortgage-orchestrator` locally and, once deployed, with `TARGET=remote ENV=<env>`.
