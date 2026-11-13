# Catalog

The catalog says which **targets** exist (agents, MCP servers, models, legacy APIs), which **team**
owns each one, which **callers** may call it, through which **gateway**, and **who pays**. It is the
source of truth for the entry-point hosts, the direct backends, the `run.invoker` /
`aiplatform.user` grants, the Agent Gateway caller lists, the caller guard allowlists, the
`principal_teams` attribution rows and `esmeralda verify` probes; later phases compile it into
Apigee products and further Agent Gateway rules.

Every document follows [`contracts/catalog.schema.json`](../contracts/catalog.schema.json) and starts with:

```yaml
schema: esmeralda.catalog
schema_version: 1
```

## Layout

```text
catalog/
├── README.md
├── sources.yaml                 # which sources the resolver merges, in order
├── teams.yaml                   # team registry: cost centres, country, external flag
├── policy.yaml                  # attribution rules, defaults, registered dimensions
├── teams/
│   ├── ai-coe/{team.yaml, agents/…, mcp/…}
│   ├── cx/{agents/…}
│   └── platform/{callers.yaml, models/…}
├── external/
│   ├── partners/…               # Apigee apps -> partner teams (example)
│   ├── clouds/…                 # federated (issuer, subject) callers (example)
│   └── legacy/…                 # managed: false endpoints, brownfield (example)
├── environments/{dev.yaml, prd.yaml}   # overrides only (dev adds the synthetic external-probe)
└── .resolved/{dev.json, prd.json}      # GENERATED, committed: what Terraform reads
```

| Document | Where | Shape |
|---|---|---|
| Target | one per file: `teams/<team>/{agents,mcp,models}/<name>.yaml`, `external/legacy/<name>.yaml` | `name`, `kind` (`mcp`, `a2a`, `adk`, `model`, `http`), `owner_team`, `deploy` (`unit`, or `managed: false` + `endpoint`), `serving` (`token`, `probe`, `timeout`), `gateway`, `callers`, `user_credential`, `models`, `observability` |
| Collection | `teams.yaml`, `policy.yaml`, `teams/<team>/callers.yaml`, `external/**` | maps under `teams`, `callers`, `targets` and/or `policy` |
| Team profile | `teams/<team>/team.yaml` | `profile: {team, description, oncall, contacts, repository_paths}` |
| Sources | `sources.yaml` | ordered `sources` list |
| Environment | `environments/<env>.yaml` | `environment`, `overrides` |

**Callers.** Agents call as themselves: a target of kind `a2a` or `adk` can be listed under
another target's `callers` directly. Agent targets don't declare an `identity`: an agent calls out
with its own Agent Identity, referenced as its unit's `agent_identity_principal` output. Every other
identity (VM, human group, partner Apigee app,
workload on another cloud, SPIFFE ID) is declared under `callers` with its `team` and `identity`
(`service_account`, `group`, `apigee_app`, `federated`, `spiffe`). Identities the platform creates are
referenced from unit outputs (`ref: layer-3-security:<output>`), never pasted by hand.

**Who pays.** The calling team. A grant's `cost_centers` limits which of its team's cost centres a
caller may declare (`policy.attribution.declared_mismatch` decides what happens otherwise).

**Examples.** Entries with `example: true` (everything under `external/` today) are resolved and
shown by `esmeralda catalog show`, but never compiled into enforcement.

## Resolve, compile, inspect

```bash
uv run esmeralda catalog resolve --env dev                  # full resolved catalog (JSON)
uv run esmeralda catalog resolve --env dev --format hcl     # the Terraform catalog map, as HCL
uv run esmeralda catalog resolve --env all --write          # regenerate catalog/.resolved/*.json
uv run esmeralda catalog resolve --env all --check          # exit 1 when a generated file is stale
uv run esmeralda catalog show --env dev                     # who can call what, via which gateway, who pays
uv run esmeralda catalog show --env dev --caller test-vm    # what one caller can call
```

The resolver:

1. merges the enabled sources of `sources.yaml` in order. Only the `files` source is implemented;
   `agent_registry` and `project_labels` are extension points (`enabled: false`), and other adapters
   (Backstage, a CMDB) register with `esmeralda.catalog.register_source`. Inside `files`, an entry
   or a policy leaf defined twice fails; a later source may only fill fields left unset (a different
   value is a conflict);
2. applies `environments/<env>.yaml` overrides (maps merge, lists replace, `null` deletes);
3. fills unset `gateway`, `user_credential` and `observability` fields from `policy.defaults`;
4. validates against the schema, then fails on unknown teams, callers, models or dimensions, on
   duplicate names or units, and on cost centres a caller's team does not own.

**Compile.** Deployed targets (`kind` `mcp`/`a2a`/`adk` with a `deploy.unit`, not examples) become
the `catalog` map in `.resolved/<env>.json`: `kind`, `unit`, `token`, `probe`, optional `timeout` and
optional `callers` (agents with a unit, granted as their Agent Identity principal, and
`service_account` callers, granted as their service account; they become `run.invoker` /
`aiplatform.user`). `infrastructure/live/<env>/env.yaml` decodes it:

```hcl
catalog_sources = "catalog/sources.yaml"
catalog         = lookup(jsondecode(file("${get_repo_root()}/catalog/.resolved/<env>.json")), "catalog")

catalog_iap_egress = lookup(jsondecode(file("${get_repo_root()}/catalog/.resolved/<env>.json")), "iap_egress")

catalog_external_targets = lookup(jsondecode(file("${get_repo_root()}/catalog/.resolved/<env>.json")), "external_targets")
```

The same callers also compile into `iap_egress`: the Agent Gateway per-target `roles/iap.egressor`
lists (`agents` for `a2a` targets, `mcp_servers` for `mcp` targets, `endpoints` for external
endpoints) the `iap-egress` unit writes.
Members are `<unit>:<output>` references (an agent caller's `agent_identity_principal`,
`service_account` callers' `ref`). The resolver also supports an optional
`policy.agent_gateway.shared_workload_identity` block that adds one shared identity to targets with an
agent caller; this repository doesn't use it, since agents call out as their own Agent Identity.
The unit maps each reference to a unit output, so one `callers`
entry drives both the invoker grants and the gateway list. Callers with `agent_gateway: false`
(such as the Apigee proxy, which calls the services directly) keep their invoker grant but are left
out of `iap_egress`.

**External targets.** A non-example target with `deploy: { managed: false, endpoint: ... }` (a
legacy API, an agent on another cloud, an external gateway) compiles into `external_targets`: the
`iap-egress` unit registers it in the Agent Registry (`http` and non-Google `model`: endpoint;
`mcp`: MCP server; `a2a`: agent card) and writes its callers' list on that entry, so only they reach
it through the Agent Gateway. A target with no enforced caller gets an empty policy, and removing it
deletes the entry. `endpoint` is an exact `https://` URL; use `{domain}` (as `<label>.{domain}`) and
`{region}` instead of environment literals. Targets on `*.googleapis.com` (such as `gemini-flash`)
are layer-4 system endpoints and get no entry of their own. dev declares the synthetic
`external-probe` in `environments/dev.yaml` to prove it.

**Verified attribution.** The same callers also compile into `attribution` (env.yaml
`catalog_attribution`):

- `policy`: `unmapped_caller` (`deny`, or `flag` only in `policy.attribution.flag_environments`,
  default `[dev]`) and `declared_mismatch` (`reject` or `bill_own_team`);
- `principal_teams`: one row per verified principal (agent principals, service accounts and other
  caller identities, `group:<email>`), with its `principal_type` (`agent_runtime`, `service_account`,
  `group`, ...), caller, team and cost centres. Callers with a unit-output identity are included
  only when a target of the environment lists them. These are
  the rows of the attribution table the usage views join;
- `shared_principals`: a shared workload identity several agents present (from
  `shared_workload_identity`), which has no row; empty in this repository, since each agent is
  attributed by its own principal;
- `allowlists`: per MCP / A2A target, the principals the in-process caller guard accepts and the
  cost centres each one may declare there. The service units write them into
  `ESMERALDA_CALLER_ALLOWLIST`.

The compile fails when a principal that gets a grant has no team, or when one principal maps to
two teams.

The runtime and Terraform never read the sources directly. After editing `catalog/`, run
`esmeralda catalog resolve --env all --write` and commit the generated files with the change;
`make test-guards` (CI) fails when they are stale.

## Ownership (CODEOWNERS)

Each team approves its own folder; platform and security approve the shared files and `external/`.
Snippet for the repository's `.github/CODEOWNERS` (team handles are placeholders):

```text
/catalog/teams/ai-coe/      @example-org/ai-coe
/catalog/teams/cx/          @example-org/cx
/catalog/teams/platform/    @example-org/platform
/catalog/external/          @example-org/platform @example-org/security
/catalog/policy.yaml        @example-org/platform @example-org/security
/catalog/sources.yaml       @example-org/platform @example-org/security
/catalog/teams.yaml         @example-org/platform
/catalog/environments/      @example-org/platform
/catalog/.resolved/         @example-org/platform
/contracts/                 @example-org/platform
```

## Adding a target

1. Add `teams/<team>/<agents|mcp>/<name>.yaml` (the name is the host label `<name>.<domain>` and
   the last segment of `deploy.unit`).
2. Add its layer-5 unit under `infrastructure/live/<env>/layer-5-workloads/` and its dependency
   block in `gateways/direct` (and `gateways/envoy` when used), as before.
3. `uv run esmeralda catalog resolve --env all --write`, then `uv run esmeralda plan` the affected units.
