# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository. Nested `AGENTS.md` files in
`infrastructure/`, `apps/agents/`, `apps/services/` and `packages/esmeralda/` add local rules; the
closest one to the file you edit wins. Full docs: <https://google.github.io/esmeralda/> (index for
LLMs: <https://google.github.io/esmeralda/llms.txt>), sources in `docs/src/`.

## What Esmeralda is

A reference blueprint for running AI agents (Google ADK, A2A) and MCP tool servers in production on
Google Cloud. Agents run as bring-your-own-container images on Vertex AI Agent Engine (Agent
Runtime) with a SPIFFE Agent Identity; all their egress goes through the Central Agent Gateway, which
only allows destinations registered in the Agent Registry. Private destinations live under
`*.esmeralda.internal` and reach an internal HTTPS load balancer (the **entry point**) that routes
each host to a **gateway** (Kong today), which forwards to the agent or MCP server. The sample use
case is a mortgage-underwriting assistant: `cx-mortgage-orchestrator` (ADK, CX team project) calls
`ai-coe-mortgage-specialist` (A2A, AI CoE team project), which calls three MCP servers.

## Repo map

| Path | What lives there |
|---|---|
| `apps/agents/<agent>/` | Agent packages: `agent.yaml` (framework, env, `local_env`, agent card), `agent/`, `tests/`, `Dockerfile` |
| `apps/services/<svc>/` | FastMCP servers (`corporate-email`, `income-verification`, `legacy-dms`) + `tools.json`; `kong/` is the custom Kong image |
| `packages/esmeralda/` | Runtime library shared by agents: `esmeralda run/serve/query`, auth, MCP/A2A clients, telemetry |
| `infrastructure/modules/` | Terraform modules, one folder per layer (`0-cicd` … `5-workloads`) |
| `infrastructure/live/` | Terragrunt units: `root.hcl`, `shared/layer-0-cicd`, `<env>/env.yaml` + `<env>/layer-*` (envs: `dev`, `prd`) |
| `scripts/` | Helpers called by Make targets (lock, preflight, gateway probes, image checks, promotion) |
| `docs/` | MkDocs site: `mkdocs.yml`, `src/` (pages), `hooks/` (Make Targets page generated from the Makefile) |
| `tests/load_test/` | Locust load test (`make load-test`) |
| `.cloudbuild/`, `.github/`, `.githooks/` | Image build config, Docs + uv.lock workflows, pre-push lock check |

## Layers and dependencies

Use **layer** (L0–L5), never "stage". Each layer reads the outputs of the layers below it through
Terragrunt `dependency` blocks; never reverse that direction.

1. **L0 CI/CD** (`live/shared/layer-0-cicd`, shared by all envs): CI/CD project, dev + release Artifact Registry repos, builder/promoter SAs.
2. **L1 Projects** → **L2 Networking** (Shared VPC, PSC, entry point IP + `esmeralda.internal` DNS) → **L3 Security** (IAM, service accounts, internal Root CA, entry point certificate).
3. **L4 Governance**: Agent Gateway, Agent Registry, Model Armor, telemetry sinks, alerts, FinOps views.
4. Images (`make build-images`), then **L5 Workloads** in this order: MCP services → agents → gateways (`gateways/kong`) → **entry point** (`entrypoint`) → `iap-egress` (+ `test-vm`).

`make deploy-workloads` runs `terragrunt run --all apply` in `layer-5-workloads`, which derives that order from the dependency blocks.

## Invariants and contracts

- **Gateway adapter contract.** A gateway module (`infrastructure/modules/5-workloads/gateways/<name>/`) outputs `backend_service_id` (a regional `INTERNAL_MANAGED` backend service) and `routes` (`host` + `probe` = `mcp`|`a2a`|`none` per host). It owns no load balancer, certificate or DNS. Any gateway change must pass `make test-gateway ENV=<env>`. See `docs/src/2-workloads-and-catalog/01-ingress-gateways.md`.
- **`gateways` block in `infrastructure/live/<env>/env.yaml`:** `enabled` (deployed gateway units), `default` (receives every host), `host_rules` (host label or FQDN → gateway). `default` and every `host_rules` value must be in `enabled`; the entrypoint module validates this.
- **Hostnames.** Agents reach MCP servers and other agents only via `https://<name>.esmeralda.internal` (MCP at `/mcp`, A2A card at `/v1/card`). Never call `run.app` or Reasoning Engine URLs directly from agent code.
- **Kong routes use agent engine IDs**: after an agent engine is recreated, re-run `make deploy-gateway ENV=<env>`.
- **Images are deployed by digest, never by tag.** `make test-terraform` fails on `image = var.…`; `make verify-images` checks running digests. Images contain no certificates (injected at deploy time), so one digest is promoted from dev to prd (`make promote TAG=vX.Y.Z`).
- **Python is one uv workspace** (root `pyproject.toml`, single `uv.lock`). The Makefile exports `UV_FROZEN=1`: never run `uv lock` or `uv add` locally. Edit `pyproject.toml`, then `make lock` (runs `uv lock` in Cloud Build against public PyPI and writes `uv.lock` back) and commit `uv.lock`. `make lock-check` (also the pre-push hook and a PR workflow) fails on a stale lock.
- **Terragrunt env vars** read by every `env.yaml`: `ESMERALDA_BILLING_ACCOUNT` (required) and `ESMERALDA_ORG_ID` (optional). Export them in your shell; never write them into a file in the repo.
- **No hardcoded project IDs** in code or docs: read them from layer outputs; write placeholders such as `esm-<env>-gateway-<sfx>`.
- **Team ownership:** CX orchestrators are `apps/agents/cx-*` (CX project); reusable A2A agents are `apps/agents/ai-coe-*` (AI CoE project).

## Commands that matter

Run `make help` for all targets. Most take `ENV=<env>` (default `dev`).

| Command | What it proves |
|---|---|
| `make bootstrap` | Tools present (preflight), workspace synced with `uv`, pre-push hook installed |
| `make lock-check` | `uv.lock` matches every `pyproject.toml` (offline) |
| `make test` | Unit tests of the MCP services, both agents and `packages/esmeralda` (`make test-agents`: agents + library only) |
| `make test-terraform` | Every module passes `terraform validate`; workloads deploy images by digest |
| `make test-all` | `lock-check` + `test` + `test-terraform`: the offline gate before a PR or a plan |
| `make docs-build` | Docs site builds in strict mode (broken links, anchors, nav fail), same as CI |
| `make query AGENT=<agent>` | The agent answers in-process against local MCP servers (`TARGET=local`, default) |
| `make serve AGENT=<agent> [WEB=1]` | The agent serves locally exactly as in its container |
| `make run-mcp-local` | The three MCP servers start on ports 8001-8003 |
| `make deploy-<layer> ENV=<env>` | Applies one layer (`cicd`, `projects`, `networking`, `security`, `governance`, `workloads`) |
| `make test-gateway ENV=<env>` | Every gateway route host answers through the entry point, from the test VM |
| `make query AGENT=cx-mortgage-orchestrator TARGET=remote ENV=<env>` | The deployed orchestrator answers a real query end to end (Agent Gateway → entry point → Kong → specialist → MCP) |
| `make test-e2e ENV=<env>` | Specialist, then orchestrator → specialist, both deployed |
| `make lock [PKG=...]` | Regenerates `uv.lock` in Cloud Build (needs `gcloud` access to the CI/CD project) |

## Testing expectations

1. **Offline first:** `make test-all` (and `make docs-build` when docs change) must pass before any plan or PR.
2. **Before an apply,** run `terragrunt plan` in the unit directory (`infrastructure/live/<env>/<layer or unit>`) and read it.
3. **After an infra, gateway or agent change in a deployed env,** prove it end to end: `make test-gateway ENV=<env>` for ingress changes, and always a real query that returns an answer: `make query AGENT=cx-mortgage-orchestrator TARGET=remote ENV=<env>` (or `make test-e2e ENV=<env>`). A routing or TLS-only check is not enough.

## Safety

- The `make deploy-*` targets apply with `-auto-approve`: review a plan first, and never apply to `prd` without explicit intent.
- Never destroy without explicit intent. `make destroy-all` refuses `prd`, asks for confirmation and never touches L0.
- With the default local state (`state_project`/`state_bucket` empty in `env.yaml`), state lives in `infrastructure/live/.local_states/`; `make clean` deletes `*.tfstate*` files, so don't run it on a machine that holds the only copy of a live env's state.
- Never commit secrets, `.env`, `*.tfvars`, Terraform state or plans, certificates or keys (`*.pem`, `*.crt`), or load-test results. They are gitignored; keep it that way.
- Use synthetic data only (the fictional applicants such as Julian Sterling). No real customer data, names or project IDs in code, docs, tests, commits or PRs.

## PR conventions

- [Conventional commits](https://www.conventionalcommits.org/): `feat(scope): …`, `fix(…)`, `docs: …`, `refactor(…)`, `chore: …`, `style: …`; imperative, lower case.
- One topic per PR; include doc updates (`docs/src/`, and a new page in `docs/mkdocs.yml` `nav`). Document new Make targets with their `## ...` help comment, not in the docs.
- Stage explicit paths (no `git add -A`); commit `uv.lock` with any `pyproject.toml` change.
- See `docs/src/contributing.md` for the CLA and the full contribution guide.
