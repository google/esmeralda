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
[`Makefile`](../../Makefile). Run `make help` to list every target. Before opening
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

## Documentation

The documentation site (<https://google.github.io/esmeralda/>) is built with
[MkDocs Material](https://squidfunk.github.io/mkdocs-material/) from
[`docs/src/`](index.md), configured in [`docs/mkdocs.yml`](../mkdocs.yml).

```bash
make docs-serve   # live preview on http://127.0.0.1:8000
make docs-build   # strict build into site/ (fails on broken links), same as CI
```

* Add a new page to the `nav` in `docs/mkdocs.yml`.
* Write plain GitHub Markdown: GitHub alerts (`> [!NOTE]`) and relative links
  to repo files (for example `../../../infrastructure/...`) are converted for
  the site at build time, so pages read well on github.com too.
* The *Make Targets* reference is generated from the Makefile's `## ...`
  comments: document a new target there, not in the docs.
* The **Docs** workflow builds every PR that touches the docs and publishes
  `main` to GitHub Pages.

## Dependencies

**What the lockfile is.** All Python dependencies (direct and transitive) of the
workspace are pinned, with hashes, in the root [`uv.lock`](../../uv.lock). Each
`pyproject.toml` only declares version *ranges*; `uv.lock` records the exact
versions that were tested.

**How images use it.** `make build-images` exports each app's exact set from
`uv.lock` into `apps/**/requirements.lock` (generated, gitignored) and the
Dockerfile installs it with `--require-hashes`. An image therefore contains
exactly what is in `uv.lock`, never whatever is newest on PyPI on build day.

**Never run `uv lock` directly.** On corporate machines `uv` goes through a
package proxy (for example Corp Airlock) that holds back new releases for 1-3
weeks and would rewrite every URL in `uv.lock`. The Makefile sets
`UV_FROZEN=1`, so `uv` only installs what `uv.lock` says; to change the lock,
use `make lock`.

**`make lock`** sends your local `pyproject.toml` files and `uv.lock` to Cloud
Build (CI/CD project), runs `uv lock` there against public PyPI, streams the
log and writes the new `uv.lock` into your working copy. Nothing is pushed:
review the diff and commit it yourself. It needs `gcloud` access to the CI/CD
project.

**How updates happen:**

| Situation | What to do |
|---|---|
| Routine and security updates | Nothing: Dependabot opens weekly PRs (7-day cooldown, OpenTelemetry and `google-*` grouped) that update `pyproject.toml` and `uv.lock`. Review and merge. |
| You edited a `pyproject.toml` (new dependency, new range) | `make lock`, then commit `uv.lock` with your change. |
| A library must be upgraded now | `make lock PKG=urllib3` (several: `PKG="urllib3 google-adk==2.11.0"`), then commit `uv.lock`. |

**Guards against a stale lock:**

* **git pre-push hook** (installed by `make bootstrap`, in `.githooks/`): runs
  `make lock-check` and blocks the push if `uv.lock` is out of date with the
  `pyproject.toml` files. The check is offline and takes milliseconds.
* **`make test-all`** runs the same check.
* The **uv.lock check** workflow runs it on every PR, for anyone without the
  hook.

`a2a-sdk` major versions are ignored by Dependabot until the A2A 1.x migration
is done.

Conventions:

* Use **layer** (L0 to L5), never "stage", for the infrastructure levels.
* Keep the team ownership model intact: CX orchestrators live in
  `apps/agents/cx-*` and the CX project; reusable A2A agents live in
  `apps/agents/ai-coe-*` and the AI CoE project.
* Never commit certificates or private keys (`*.pem` and `*.crt` are
  gitignored). Certificates are injected at deploy time (see the
  [Central Agent Gateway guide](3-agentops-and-lifecycle/01-central-agent-gateway.md#6-bring-your-own-container-byoc)).
* Don't hardcode project IDs in code or docs: read them from layer outputs, and
  write placeholders such as `esm-<env>-governance-<sfx>` in documentation.
* **Deploy images by digest, never by tag.** Cloud Run and Agent Runtime read a
  tag only when a revision is created, so a template that keeps saying
  `:dev-latest` never rolls out a rebuilt image. Every Layer 5 workload resolves
  the tag to its digest at plan time (`services/*/image.tf` →
  `local.image_by_digest`; the `google_artifact_registry_docker_image` data
  source in `agents/*`). Copy that pattern for new workloads:
  `make test-terraform` fails if a module deploys `image = var.…` directly, and
  `deploy-workloads` ends with `make verify-images`, which compares the digest
  each workload is running with the digest its tag points to.
