# How to Contribute

We would love to accept your patches and contributions to this project.

## Before you begin

### Sign our Contributor License Agreement

Contributions to this project must be accompanied by a
[Contributor License Agreement](https://cla.developers.google.com/about) (CLA).
You (or your employer) retain the copyright to your contribution; this simply
gives us permission to use and redistribute your contributions as part of the
project.

If you or your current employer have already signed the Google CLA (even if it
was for a different project), you probably don't need to do it again.

Visit <https://cla.developers.google.com/> to see your current agreements or to
sign a new one.

### Review our Community Guidelines

This project follows [Google's Open Source Community
Guidelines](https://opensource.google/conduct/).

## Contribution process

### Code Reviews

All submissions, including submissions by project members, require review. We
use [GitHub pull requests](https://docs.github.com/articles/about-pull-requests)
for this purpose.

## Local development and testing

Esmeralda is a [`uv`](https://docs.astral.sh/uv/) workspace driven by the root
[`Makefile`](../Makefile). Run `make help` to list every target. Before opening
a pull request, run at least:

```bash
make bootstrap    # preflight checks + install all workspace packages with uv
make test-all     # Python unit tests (MCP services + both agents) and `terraform validate` on every module
```

If you changed agent or MCP code, also exercise it locally (the targets start
and stop the three MCP servers on ports 8001-8003 for you):

```bash
make test-ai-coe-mortgage-specialist-local   # AI CoE specialist (A2A) -> local MCP servers
make test-cx-mortgage-orchestrator-local     # CX orchestrator -> specialist -> local MCP servers
```

If you have a deployed `dev` environment, `make test-e2e ENV=dev` checks the
specialist and then the orchestrator -> specialist path through the Agent
Gateway and Kong.

## Dependencies

**What the lockfile is.** All Python dependencies (direct and transitive) of the
workspace are pinned, with hashes, in the root [`uv.lock`](../uv.lock). Each
`pyproject.toml` only declares version *ranges*; `uv.lock` records the exact
versions that were tested.

**How images use it.** `make build-images` exports each app's exact set from
`uv.lock` into `apps/**/requirements.lock` (generated, gitignored) and the
Dockerfile installs it with `--require-hashes`. An image therefore contains
exactly what is in `uv.lock`, never whatever is newest on PyPI on build day.

**Never re-lock locally.** The Makefile sets `UV_FROZEN=1`, so `uv` installs
from `uv.lock` without re-resolving. `uv.lock` is always generated against
public PyPI on GitHub; running `uv lock` on a machine that uses a different
package index (for example a corporate proxy) would rewrite every URL in it.

**How updates happen:**

| Situation | What to do |
|---|---|
| Routine and security updates | Nothing: Dependabot opens weekly PRs (7-day cooldown, OpenTelemetry and `google-*` grouped) that update `pyproject.toml` and `uv.lock`. Review and merge. |
| You edited a `pyproject.toml` (new dependency, new range) | Run **Actions → Update uv.lock → Run workflow** on your branch with `packages` empty, then merge the pushed `chore/uv-lock-<run>` branch into your PR. |
| A library must be upgraded now | Same workflow, with `packages` set, e.g. `urllib3` or `google-adk==2.11.0`. Open a PR from the pushed branch. |

The **uv.lock check** workflow fails any PR whose `pyproject.toml` files and
`uv.lock` are out of sync. `a2a-sdk` major versions are ignored by Dependabot
until the A2A 1.x migration is done.

Conventions:

* Use **layer** (L0 to L5), never "stage", for the infrastructure levels.
* Keep the team ownership model intact: CX orchestrators live in
  `apps/agents/cx-*` and the CX project; reusable A2A agents live in
  `apps/agents/ai-coe-*` and the AI CoE project.
* Never commit certificates or private keys (`*.pem` and `*.crt` are
  gitignored). Certificates are injected at deploy time (see the
  [Central Agent Gateway guide](3-agentops-and-lifecycle/01-central-agent-gateway.md#-6-bring-your-own-container-byoc)).
* Don't hardcode project IDs in code or docs: read them from layer outputs, and
  write placeholders such as `esm-<env>-governance-<sfx>` in documentation.
