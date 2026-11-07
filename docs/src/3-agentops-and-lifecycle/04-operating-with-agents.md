# 04. Operating with Agents & the `esmeralda` CLI

Coding agents, CI pipelines, and platform engineers all need operations that never prompt interactively, return compact summaries instead of raw Terraform or log dumps, support `--json` for machine parsing, and refuse destructive changes unless explicitly confirmed.

Esmeralda provides a single operational engine in [`esmeralda.ops`](../../../packages/esmeralda/src/esmeralda/ops/__init__.py) exposed in three ways:

1. **The `esmeralda` CLI** (`uv run esmeralda <subcommand> [--json]`): human-readable output by default, `--json` for automation.
2. **Slim `Makefile` targets** (`make status`, `make doctor`, `make units`, `make plan`, `make apply`, `make verify`, `make logs`, `make guard`): thin wrappers that delegate directly to `uv run esmeralda ...`.
3. **The read-only MCP server** (`esmeralda mcp`): exposes the read-only operations over stdio for coding agents (see the [Esmeralda Library Usage Guide](../4-esmeralda-library/guide.md#operating-a-checkout-from-an-agent-esmeralda-mcp)).

---

## CLI Subcommands & `make` Targets

Every operational subcommand accepts `--repo PATH` (defaults to `$ESMERALDA_REPO` or the git checkout root) and `--json`.

| CLI Command | `make` Target | What it does |
| :--- | :--- | :--- |
| `esmeralda status [--env dev\|prd] [--json]` | `make status ENV=dev` | Shows git branch, HEAD SHA, dirty/clean state, active git worktrees, local state units in `infrastructure/live/.local_states/<env>`, image tags/digests vs HEAD, and deployed unit outputs. |
| `esmeralda doctor [--env dev\|prd] [--json]` | `make doctor ENV=dev` | Preflight check: verifies required tools (`git`, `uv`, `terraform`, `terragrunt`, `gcloud`), `ESMERALDA_BILLING_ACCOUNT` (warns if unset), ADC token availability, `uv.lock` frozen check, and local state readability. Exits `1` if required checks fail. |
| `esmeralda units [--env dev\|prd] [--layer 0..5] [--json]` | `make units ENV=dev` | Lists all Terragrunt units for `<env>`, their layer (`0..5`), dependencies, whether enabled or excluded in `env.yaml`, and whether local state exists. |
| `esmeralda plan <unit\|--all> [--env dev\|prd] [--json]` | `make plan [UNIT=...] ENV=dev` | Runs `terragrunt plan -lock=false` on a unit (full path like `layer-5-workloads/entrypoint` or short name `entrypoint`) or `--all` enabled units, and prints the resource change summary (`to add`, `to change`, `to destroy`, `to replace`). |
| `esmeralda apply <unit> [--env dev\|prd] [--allow-destroy] [--json]` | `make apply UNIT=... ENV=dev [ALLOW_DESTROY=1]` | Plans `<unit>` to a planfile first, inspects the plan for `destroy` or `replace` actions, **refuses to apply with exit code `2` if `destroy > 0` unless `--allow-destroy` is passed**, and otherwise applies the saved planfile. |
| `esmeralda verify [--env dev\|prd] [--probe <name>] [--skip-query] [--json]` | `make verify ENV=dev` | Runs gateway conformance probes (`scripts/test_gateway.sh`) and, unless `--skip-query` is passed, runs remote queries against `ai-coe-mortgage-specialist` and `cx-mortgage-orchestrator`. Exits `1` if any check fails. |
| `esmeralda logs <target> [--env dev\|prd] [--minutes 30] [--severity DEFAULT] [--limit 50] [--json]` | `make logs TARGET_NAME=... ENV=dev` | Pulls recent Cloud Logging entries for an agent (Reasoning Engine) or MCP/gateway service (Cloud Run), collapsing tracebacks and deduplicating repeats. |
| `esmeralda contracts [--section all\|entrypoint\|gateway_adapter\|agent_card\|env_yaml] [--json]` | — | Prints the architectural contracts (`entrypoint`, `gateway_adapter`, `agent_card`, `env_yaml`) and each environment's current `gateways` configuration. |
| `esmeralda guard [--staged \| --diff <range> \| --all] [--json]` | `make guard` | Public-repository sensitive-content scanner across staged changes, a diff range, or all tracked files. Exits `1` if any secret, forbidden path, or internal/customer marker is detected. |

---

## Plan & Apply with the Destroy Guard

```bash
# Plan a single unit by short name or full path (or omit UNIT for --all)
make plan UNIT=entrypoint ENV=dev
uv run esmeralda plan entrypoint --env dev --json

# Plan-first apply with the destroy guard
make apply UNIT=entrypoint ENV=dev
```

`esmeralda apply <unit>` always plans `<unit>` to `.esmeralda/plans/<env>/<slug>.tfplan` first and inspects `terragrunt show -json` before touching cloud resources:

* **Safe plans (`destroy == 0` and `replace == 0`)**: applies the exact saved planfile (`terragrunt apply <planfile>`) and removes the single-use planfile afterward.
* **Destructive plans (`destroy > 0` or `replace > 0`)**: **refuses to apply with exit code `2`** and lists the resources that would be deleted or replaced. To proceed intentionally, pass `--allow-destroy` (or `ALLOW_DESTROY=1` with `make apply`).

---

## Sensitive-Content Guard (`esmeralda guard`)

Because Esmeralda is a public reference blueprint, `make bootstrap` (or `make hooks`) configures `core.hooksPath` to `.githooks`, wiring `esmeralda guard` into Git:

* **`.githooks/pre-commit`**: runs `uv run --frozen --package esmeralda esmeralda guard --staged` on staged diffs and file paths.
* **`.githooks/commit-msg`**: runs `uv run --frozen --package esmeralda esmeralda guard --message-file "$1"` on the commit message.
* **`.githooks/pre-push`**: runs `scripts/check_lock.sh` to verify `uv.lock` is in sync.
* **Full repository scan**: `make guard` (or `uv run esmeralda guard --all`) scans every tracked file in the repository.

You can also add local, untracked regex patterns (one case-insensitive regex per line, `#` comments) to `$(git rev-parse --git-common-dir)/info/sensitive-patterns` or point `ESMERALDA_SENSITIVE_PATTERNS_FILE` at a local file outside the repository. To bypass the hook once for a reviewed false positive, set `SKIP_SENSITIVE_SCAN=1`.
