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
each host to the built-in **`direct`** backend by default (or an optional gateway adapter such as
Envoy), which forwards to the agent or MCP server. The sample use case is a mortgage-underwriting
assistant: `cx-mortgage-orchestrator` (ADK, CX team project) calls `ai-coe-mortgage-specialist`
(A2A, AI CoE team project), which calls three MCP servers.

## Repo map

| Path | What lives there |
|---|---|
| `apps/agents/<agent>/` | Agent packages: `agent.yaml` (framework, env, `local_env`, agent card), `agent/`, `tests/`, `Dockerfile` |
| `apps/services/<svc>/` | FastMCP servers (`corporate-email`, `income-verification`, `legacy-dms`) + `tools.json`; `envoy/` is the digest-pinned Envoy adapter image |
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
4. Images (`make build-images`), then **L5 Workloads** in this order: MCP services → agents → gateways (`gateways/direct` + any enabled adapter) → **entry point** (`entrypoint`) → `iap-egress` (+ `test-vm`).

`make deploy-workloads` runs `terragrunt run --all apply` in `layer-5-workloads`, which derives that order from the dependency blocks.

## Invariants and contracts

- **Gateway adapter contract.** A gateway module (`infrastructure/modules/5-workloads/gateways/<name>/`) outputs `backend_service_id` (a regional `INTERNAL_MANAGED` backend service, or `null` for `direct`) and `routes` (`host` + `probe` = `mcp`|`a2a`|`query`|`none` per host, plus per-route `backend_service_id` and rewrite rules for `direct`). It owns no load balancer, certificate or DNS. Any gateway change must pass `make test-gateway ENV=<env>`. See `docs/src/2-workloads-and-catalog/01-ingress-gateways.md`.
- **`catalog` and `gateways` blocks in `infrastructure/live/<env>/env.yaml`:** `catalog` declares every MCP service and agent (`kind`, `unit`, `token`, `probe`, `callers`); `gateways` sets `default` (`"direct"` or an enabled adapter), `enabled` (optional adapter units such as `"envoy"`), and `host_rules` (host label or FQDN → `"direct"` or an enabled adapter). The entrypoint module validates these.
- **Hostnames.** Agents reach MCP servers and other agents only via `https://<name>.esmeralda.internal` (MCP at `/mcp`, A2A card at `/v1/card`). Never call `run.app` or Reasoning Engine URLs directly from agent code.
- **Gateway routes use agent engine IDs**: after an agent engine is recreated, re-run `make deploy-gateway ENV=<env>` and `make deploy-entrypoint ENV=<env>`.
- **Images are deployed by digest, never by tag.** `make test-terraform` fails on `image = var.…`; `make verify-images` checks running digests. Images contain no certificates (injected at deploy time), so one digest is promoted from dev to prd (`make promote TAG=vX.Y.Z`).
- **Python is one uv workspace** (root `pyproject.toml`, single `uv.lock`). The Makefile exports `UV_FROZEN=1`: never run `uv lock` or `uv add` locally. Edit `pyproject.toml`, then `make lock` (runs `uv lock` in Cloud Build against public PyPI and writes `uv.lock` back) and commit `uv.lock`. `make lock-check` (also the pre-push hook and a PR workflow) fails on a stale lock.
- **Terragrunt env vars** read by every `env.yaml`: `ESMERALDA_BILLING_ACCOUNT` (required) and `ESMERALDA_ORG_ID` (optional). Put them in the user-local env file `~/.config/esmeralda/env` (or the path in `$ESMERALDA_ENV_FILE`), **outside the repo**, `chmod 600`, never committed: plain `KEY=VALUE` lines, no quotes, only `ESMERALDA_*` keys. The `esmeralda` CLI (`plan`, `apply`, `status`, `units`, `verify`, `doctor`, `mcp`) and the Makefile both read it, so no `bash -ic` wrapper or `~/.bashrc` export is needed; variables already exported in the environment win. `esmeralda doctor` reports where each one comes from (env / env-file / missing) without printing values. Never write these values into a file in the repo.
- **Git worktrees and local state.** With the local backend, Terragrunt state lives under `infrastructure/live/.local_states/` of the checkout that applied it, so a worktree has none. Image builds (`make build-*`) read layer-0 outputs from the main checkout when the worktree has no layer-0 state (override with `CICD_DIR=<checkout>/infrastructure/live/shared/layer-0-cicd`), fail early with a clear message when no outputs are found, and `make deploy-cicd` refuses to run from such a worktree. For other units, point the CLI at the checkout that holds the state (`esmeralda plan ... --repo <checkout>`).
- **No hardcoded project IDs** in code or docs: read them from layer outputs; write placeholders such as `esm-<env>-gateway-<sfx>`.
- **Team ownership:** CX orchestrators are `apps/agents/cx-*` (CX project); reusable A2A agents are `apps/agents/ai-coe-*` (AI CoE project).

## Commands that matter

Prefer the `esmeralda` CLI (`uv run esmeralda --help`) for day-to-day status, planning, verification, querying, logging, and repository guards; `make` targets wrap the CLI and multi-step image/Terragrunt workflows (`make help`, most take `ENV=<env>`, default `dev`).

| Command | Make equivalent | What it proves |
|---|---|---|
| `uv run esmeralda status --env <env>` | `make status ENV=<env>` | Git branch/SHA, active worktrees, local state units, image tags/digests, and deployed environment outputs |
| `uv run esmeralda doctor --env <env>` | `make doctor ENV=<env>` | Workstation preflight check: required tools, user-local env file and the source of `ESMERALDA_BILLING_ACCOUNT` / `ESMERALDA_ORG_ID`, ADC token, `uv.lock`, and local state readability |
| `uv run esmeralda units [--layer 0..5] --env <env>` | `make units ENV=<env>` | Lists Terragrunt units, layer, dependencies, enabled/excluded status, and local state presence |
| `uv run esmeralda plan <unit\|--all> --env <env>` | `make plan [UNIT=<unit>] ENV=<env>` | Read-only `terragrunt plan -lock=false` summary (`to add`, `to change`, `to destroy`, `to replace`) |
| `uv run esmeralda apply <unit> --env <env>` | `make apply UNIT=<unit> ENV=<env>` | Plans to a saved planfile, **enforces the destroy guard** (refuses `destroy > 0` without `--allow-destroy`), and applies |
| `uv run esmeralda verify --env <env>` | `make verify ENV=<env>` | Gateway conformance probes (`scripts/test_gateway.sh`) + remote specialist and orchestrator queries |
| `uv run esmeralda verify --partner --env <env>` | `make test-partner ENV=<env>` | Apigee partner front-door conformance probes (`scripts/test_partner.sh`, offline or live) |
| `uv run esmeralda verify --model-armor --env <env>` | — | Model Armor checks for `governance.model_armor`: injection through model calls, gateway `tools/call` inspection, long-call regression |
| `uv run esmeralda query --agent-dir apps/agents/<agent> "..."` | `make query AGENT=<agent> [TARGET=local\|remote]` | Queries an ADK or A2A agent in-process, over `--url`, or on Agent Runtime (`--engine`), with `--user-token` support |
| `uv run esmeralda logs <target> --env <env>` | `make logs TARGET_NAME=<target> ENV=<env>` | Recent Cloud Logging entries for an agent or MCP service (tracebacks collapsed, deduplicated) |
| `uv run esmeralda contracts [--section ...]` | — | Prints architectural contracts (`entrypoint`, `gateway_adapter`, `agent_card`, `env_yaml`) |
| `uv run esmeralda guard [--staged\|--diff <range>\|--all]` | `make guard` | Public-repository sensitive-content and forbidden-path scanner |
| — | `make bootstrap` | Tools present (preflight), workspace synced with `uv`, `.githooks` installed |
| — | `make lock-check` | `uv.lock` matches every `pyproject.toml` (offline) |
| — | `make test-all` | `lock-check` + `test` + `test-guards` + `test-terraform` + `test-gateway-unit` + `test-apigee`: the offline gate before a PR |
| — | `make docs-build` | Docs site builds in strict mode (broken links, anchors, nav fail), same as CI |

## Testing expectations

1. **Offline first:** `make test-all` (and `make docs-build` when docs change) and `uv run esmeralda guard --staged` must pass before any commit, plan, or PR.
2. **Before an apply,** run `uv run esmeralda plan <unit> --env <env>` (or `uv run esmeralda apply <unit> --env <env>`, which plans first and enforces the destroy guard) and inspect the summary.
3. **After an infra, gateway or agent change in a deployed env,** prove it end to end with `uv run esmeralda verify --env <env>` (which runs all gateway route probes plus real `ai-coe-mortgage-specialist` and `cx-mortgage-orchestrator` queries), and `uv run esmeralda verify --partner --env <env>` when partner front-door capabilities are modified. A routing or TLS-only check is not enough.

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
