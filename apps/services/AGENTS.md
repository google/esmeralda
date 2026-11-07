# apps/services/AGENTS.md

Local rules for MCP tool servers. The root `AGENTS.md` still applies. Background:
`docs/src/2-workloads-and-catalog/02-mcp-tool-servers.md`. `kong/` is the custom Kong image (see
its `README.md`), not an MCP server.

## Server conventions

- One directory per server (`corporate-email`, `income-verification`, `legacy-dms`), each a uv workspace member listed in the root `pyproject.toml`.
- FastMCP app mounted at `/mcp` (streamable HTTP, stateless, JSON responses) inside a Starlette app with a `/health` route; served by uvicorn on port 8080 in the container. Follow `income-verification/main.py`.
- Telemetry through the local `otel_setup.py` (`init_telemetry`, `trace_tool`).
- `tools.json` lists the tools (`name`, `description`, `inputSchema`). It is published as the server's Agent Registry tool spec, so keep it in sync with the `@mcp.tool()` functions.
- Unit tests next to the code (`test_main.py` / `test_server.py`), wired into `make test`.
- Data is synthetic only (e.g. the fictional Sterling applicants). Never add real personal data.
- `Dockerfile`: `python:3.12-slim`, uv copied from the pinned `ghcr.io/astral-sh/uv` image, `requirements.lock` installed with `--require-hashes`, non-root user. Keep that uv version in sync with `.github/workflows/*.yml`.

## Deployment contract (`infrastructure/modules/5-workloads/services/<name>/`)

- Cloud Run with `ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"`: reachable only through the entry point and the gateway.
- **Custom audiences:** the service accepts ID tokens for `http(s)://<name>.esmeralda.internal` and `.../mcp` (plus legacy `*.internal.gateway` names) in addition to its `run.app` URI. A new server must list its own hostnames in `custom_audiences`.
- `roles/run.invoker` only for `invoker_service_accounts` (the gateway SA is the normal path).
- Image resolved to a digest in `image.tf` (`local.image_by_digest`).
- **Registry entry:** `google_agent_registry_service.governance_mcp` in the governance project with interface `https://<name>.esmeralda.internal/mcp` (`JSONRPC`) and `mcp_server_spec.content = file(var.tools_spec_path)` (`apps/services/<name>/tools.json`). Its `registry_service_id` output must be added to the `iap-egress` unit, or agents can't reach it through the Agent Gateway.
- `register_mcp.py` only writes a local, gitignored `apps/services/mcp_registry.json`; it calls no Google API.

## Adding a server

1. `apps/services/<name>/` (app, `tools.json`, tests, `pyproject.toml`, `Dockerfile`, `.dockerignore`); add it to the workspace members, then `make lock` and commit `uv.lock`.
2. Make targets: `build-service-<name>` (and `build-services`), tests in `make test`, ports in `make run-mcp-local` and the agents' `local_env`.
3. Module + live units (dev and prd) under `layer-5-workloads/services/<name>`, an entry in the gateway's `agent_endpoints` (`probe = "mcp"`), and the `iap-egress` registry list.
4. Verify: `make test`, `make test-terraform`, then after deploy `make test-gateway ENV=<env>` and a real `make query AGENT=cx-mortgage-orchestrator TARGET=remote ENV=<env>`.
