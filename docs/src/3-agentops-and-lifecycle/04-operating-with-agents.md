# 04. Operating with Agents / Automation

Coding agents, CI jobs and people in a hurry all need the same thing from the Makefile: commands that never prompt, print only what matters, return meaningful exit codes, and refuse anything risky unless explicitly asked. This page describes the targets built for that. They are thin wrappers over standard-library scripts in [`scripts/`](../../../scripts/), so they also work when called directly.

| Target | What it does | Exit code |
| :--- | :--- | :--- |
| `make doctor [ENV=dev]` | Workstation check, one line per check (✅ / ❌ / ➖ + fix hint) | 1 if any ❌ |
| `make plan UNIT=<unit> ENV=<env> [FULL=1]` | Plans one unit, prints a compact summary, saves the plan | 0 no changes, 2 changes or error |
| `make apply UNIT=<unit> ENV=<env> [ALLOW_DESTROY=1]` | Applies **only** the saved plan | 0 applied, 2 refused or failed |
| `make status ENV=<env> [JSON=1] [ALL=1] [HEALTH=1]` | Key outputs of every deployed unit, in well under a second | 0 if anything is deployed |
| `make verify ENV=<env> [VERBOSE=1]` | Gateway probes + specialist + orchestrator queries, one table | 0 only if every step passes |

`<unit>` is a path under `infrastructure/live/<env>/`, e.g. `layer-3-security` or `layer-5-workloads/gateways/kong`.

> [!NOTE]
> **Exit codes through make.** GNU make exits with 2 whenever a recipe fails, so through `make` "changes" and "error" both surface as 2 (make's message shows the script's own code: `Error 2` for changes, `Error 1` for an error). For the exact codes, call the script: `uv run --no-project python scripts/tf_plan.py plan --env dev --unit layer-3-security` returns 0 (no changes), 2 (changes) or 1 (error).

## Quiet, non-interactive defaults

The Makefile exports `TF_IN_AUTOMATION=1`, `TG_NON_INTERACTIVE=true` and `TG_LOG_LEVEL=warn` for every target (Terraform's own output is still shown). Override any of them from the environment, e.g. `TG_LOG_LEVEL=info make deploy-security`. The plan/apply scripts also set `TF_INPUT=0`, `TG_NO_COLOR` and `TG_TF_FORWARD_STDOUT`.

## Plan, review, apply

```bash
make plan UNIT=layer-5-workloads/gateways/kong ENV=dev
```

```text
📋 dev/layer-5-workloads/gateways/kong: 1 update, 1 output(s)
    ~ module.kong.google_cloud_run_v2_service.kong  update [template]
  outputs: ~routes
  saved: .plans/dev/layer-5-workloads/gateways/kong/{plan.txt,plan.json,plan.tfplan} (41s)
```

* One line per resource that changes, destructive actions first: `+` create, `~` update, `-/+` replace, `-` delete, `<=` read. Updates list the **names** of the changed top-level attributes, replaces list the attributes that force the replacement. Values are never printed, so sensitive values cannot leak into a log or a chat transcript.
* The full plan text, the `terraform show -json` document and the binary plan are saved in `.plans/<env>/<unit>/` (gitignored). `FULL=1` also prints the full text.
* The plan runs with `-lock=false`: it never blocks a concurrent apply.

```bash
make apply UNIT=layer-5-workloads/gateways/kong ENV=dev
```

`make apply` prints the saved summary first, then applies exactly that plan file (`terragrunt apply <planfile>`), so what was reviewed is what runs. It refuses to run when:

* there is no saved plan for the unit (run `make plan` first);
* the plan deletes or replaces anything, unless `ALLOW_DESTROY=1`;
* the plan errored.

Terraform itself rejects a plan that went stale because the state changed after it was made. A plan is single-use: it is deleted after a successful apply.

## Status

```bash
make status ENV=dev            # table of the key outputs
make status ENV=dev JSON=1     # every output of every unit, as JSON
make status ENV=dev HEALTH=1   # ... then make test-gateway
```

`make status` reads the Terraform state outputs directly instead of running `terragrunt output` per unit (which resolves every dependency and takes seconds each): from `infrastructure/live/.local_states/` with the local backend, or with parallel `gcloud storage cat` of the GCS state objects (falling back to `terragrunt output -json`) when `state_project` / `state_bucket` are set in `env.yaml`. It shows the entry point address and domain, the routing, each gateway's backend service and routes, agent engine IDs, MCP service URIs, the test VM, and the image digests found in the state. `ALL=1` lists every output.

## Verify

```bash
make verify ENV=dev
```

```text
STEP          RESULT  TIME
------------  ------  ------
gateway       PASS       25s
specialist    PASS       20s
orchestrator  PASS       35s

✅ dev verified end to end
```

Runs `make test-gateway`, then `make query AGENT=ai-coe-mortgage-specialist TARGET=remote` and `make query AGENT=cx-mortgage-orchestrator TARGET=remote`. Every step runs even if an earlier one fails, so one call shows the whole picture. Only the agents' answers (first lines), the tail of failed steps and the table are printed; full logs are in `.plans/<env>/_verify/`. A query passes only if the agent returns a real text answer.

## Doctor

`make doctor` complements `make preflight` (fail-fast) with a full, non-fatal report: tool versions (Terraform, Terragrunt, gcloud, uv, jq, and OpenTofu if present), the active gcloud account and token, the active project, ADC validity and quota project, every environment variable `env.yaml` reads with `get_env()` (set / unset only, values are never printed), the git hooks, the local sensitive-patterns file, free disk space (repository and the `TF_PLUGIN_CACHE_DIR` / `TG_DOWNLOAD_DIR` / `UV_CACHE_DIR` caches), and finally the existing preflight (including billing) reduced to one line.

## Sensitive-content commit hook

This repository is public. `make bootstrap` (or `make hooks`) sets `core.hooksPath` to `.githooks`, which adds:

* **pre-commit**: scans the lines added by the staged diff, and the staged file names;
* **commit-msg**: applies the same content rules to the commit message;
* **pre-push** (existing): checks that `uv.lock` is up to date.

Built-in rules are generic: PEM blocks and private keys, common token formats (Google API keys and OAuth tokens, GitHub, AWS, Slack, long bearer tokens) and files that must never be committed (`*.tfvars`, Terraform state and plans, `.env` files, key files, local agent session state, `.local_states`). Findings are reported as `file:line: rule [masked snippet]`.

Add your own patterns, e.g. organisation, customer, project or host names that must never appear here, to a **local, untracked** file, one Python regex per line (case-insensitive, `#` comments):

```bash
# Default location, shared by every worktree of the clone:
f="$(git rev-parse --git-common-dir)/info/sensitive-patterns"
cat >> "$f" <<'EOF'
# one regex per line
acme[ -]?corp
my-dev-project-[0-9]+
internal\.example\.com
EOF
# ... or anywhere else:
export ESMERALDA_SENSITIVE_PATTERNS_FILE=~/.config/esmeralda/sensitive-patterns
```

Never commit real patterns; the built-in rules stay generic. To bypass the hook once (e.g. a false positive you have reviewed): `SKIP_SENSITIVE_SCAN=1 git commit ...`.
