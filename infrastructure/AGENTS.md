# infrastructure/AGENTS.md

Local rules for Terraform modules and Terragrunt units. The root `AGENTS.md` still applies.

## Layout

- `modules/<n>-<layer>/`: Terraform modules (`main.tf`, `variables.tf`, `outputs.tf`, optional `versions.tf`). Layer 5 is split into `5-workloads/{services,agents,gateways,entrypoint,apihub}/<name>/`.
- `live/root.hcl`: included by every unit. Generates `provider.tf` (google + google-beta, region from `env.yaml`) and `backend.tf`.
- `live/<env>/env.yaml`: per-env settings (prefix, region, state backend, BYO toggles, images, `gateways`, monitoring). `live/shared/env.yaml` serves L0.
- `live/<env>/layer-*/.../terragrunt.hcl`: one unit per module instance. `dev` and `prd` have the same unit tree; keep them in sync.

## Unit conventions

Every unit:

```hcl
include "root" {
  path = find_in_parent_folders("root.hcl")
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

terraform {
  source = "../../../../../modules//5-workloads/services/<name>"   # note the `//`
}
```

- Inputs come from `local.env_vars.locals.*` and `dependency.<x>.outputs.*`. Never hardcode project IDs, SA emails or URLs.
- Paths to repo files use `get_repo_root()` (e.g. `tools_spec_path`, `agent_config_path`).
- Optional units use an `exclude { if = ..., actions = ["all"] }` block driven by `env.yaml` (see `gateways/kong`).

## Dependencies and mocks

- Depend only on lower layers, or on earlier L5 units (services → agents → gateways → entrypoint → iap-egress).
- When a unit must `validate`/`plan` before its dependency has been applied, add `mock_outputs` with `mock_outputs_allowed_terraform_commands = ["validate", "plan"]` (and `mock_outputs_merge_strategy_with_state = "shallow"` where outputs may be partially present). Mocks must never reach `apply`.
- A dependency that is conditionally used has `enabled = <condition>` and is read with `try(dependency.x.outputs.y, null)` (see `layer-5-workloads/entrypoint/terragrunt.hcl`).

## Adding a unit

1. Write or reuse the module under `modules/`; declare typed variables with descriptions and expose the outputs other units need.
2. Add the unit under `live/dev/...` and the same unit under `live/prd/...`.
3. Workloads with an image resolve the tag to a digest at plan time (`services/*/image.tf` → `local.image_by_digest`; the `google_artifact_registry_docker_image` data source in `agents/*`). Never `image = var.…`.
4. Anything agents must reach needs an Agent Registry entry and must be listed in `layer-5-workloads/services/iap-egress` (`registry_service_ids`).
5. If it needs a Make target, add it to the root `Makefile` with a `## help` comment.
6. `make test-terraform`, then `terragrunt plan` in the unit directory.

## Adding a gateway adapter

1. Module `modules/5-workloads/gateways/<name>/` with outputs `backend_service_id` (regional `INTERNAL_MANAGED` backend service in the gateway project) and `routes` (`host`, `probe` per host). No load balancer, certificate or DNS record.
2. Live unit `live/<env>/layer-5-workloads/gateways/<name>/` with an `exclude` block when `<name>` is not in `gateways.enabled`.
3. In `live/<env>/layer-5-workloads/entrypoint/terragrunt.hcl`: one `dependency "<name>"` block (`enabled = contains(local.env_vars.locals.gateways.enabled, "<name>")`, mocked `backend_service_id`) plus its entry in `inputs.backends`.
4. Enable it in `env.yaml` `gateways` (move hosts one at a time with `host_rules`), apply the gateway, then `make deploy-entrypoint ENV=<env>`, then `make test-gateway ENV=<env>` and a real `make query ... TARGET=remote`.

Reference: `modules/5-workloads/gateways/kong/` and `docs/src/2-workloads-and-catalog/01-ingress-gateways.md`. `gateways/apigee` is a stub (`backend_service_id = null`) with no live unit.

## State

- Backend is chosen in `root.hcl`: GCS when `state_project` and `state_bucket` are set in `env.yaml`, otherwise local files under `live/.local_states/<unit path>/terraform.tfstate`.
- State, plans, `.terraform/`, `.terragrunt-cache/` and `.terraform.lock.hcl` are gitignored; never commit them. `make clean` deletes local `*.tfstate*` files.
- Never edit state by hand or run `terraform state rm/mv` / `import` without explicit intent.
