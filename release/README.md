# `release/`: release manifests

`release/<env>.yaml` is the GitOps release manifest of an environment: the exact image bytes it
deploys, as one `sha256` digest per Layer 5 workload image. Only `prd.yaml` exists today; `dev`
deploys tags from the mutable dev repository and has no manifest.

> **Never edit these files by hand.** They are written by `esmeralda release promote` and approved
> by pull request. A hand-edited digest may not exist in the release repository, or may not be the
> build that passed dev verification.

## Lifecycle

| Step | Who / what | Details |
|---|---|---|
| Write | `uv run esmeralda release promote --version vX.Y.Z --source dev-<gitsha> [--verified YYYY-MM-DD]` (`make promote VERSION=… SOURCE=… VERIFIED=…`) | After `esmeralda verify --env dev` passes, resolves the digest of every image tagged `dev-<gitsha>`, copies those digests into the immutable release repository as `vX.Y.Z`, and writes `release/prd.yaml`. Re-running the same promotion is a no-op; reusing a version with different bytes is refused. `--dry-run` checks everything and shows what would change, without copying or writing. It does not deploy. |
| Review | Pull request | The PR diff shows which images changed, from which commit, and when dev verified them. The file holds no project ids or registry URLs (the registry comes from the layer-0 Terragrunt outputs). Merging the PR is the release approval. |
| Read | `infrastructure/live/prd/env.yaml` | `release_manifest` loads `release/prd.yaml`; `image_digests` (its `images`) is passed to every prd Layer 5 unit, which deploys `<release repository>/<image>@sha256:…`. While the file does not exist (a fork that never promoted), prd falls back to `container_tag`. |
| Read | Running-image check: `uv run esmeralda build --verify --env prd` (`make verify-images ENV=prd`) | Compares the digest every Cloud Run service and Agent Runtime engine is running with the manifest. Also runs at the end of every `esmeralda deploy --layer 5`. |
| Read | `uv run esmeralda release status` (`make status-release`) | Prints the manifest (version, source, digests) and the shared repositories. |
| Deploy | `uv run esmeralda deploy --layer 5 --env prd` (`--dry-run` first) | Run after the release PR is merged. |

## Rolling back

Revert the release PR (`git revert <merge commit>`), merge the revert, and deploy:

```bash
uv run esmeralda deploy --layer 5 --env prd --dry-run
uv run esmeralda deploy --layer 5 --env prd
uv run esmeralda build --verify --env prd
```

The release repository is immutable, so every earlier digest is still available.

## Schema

The header of each manifest documents the schema (version 1): `version`, `source_tag`,
`source_commit`, `promoted_at`, `repository`, `verified` (environment, date, result) and `images`
(image name to digest). The full release flow is described in
[From `dev` to `prd`: Immutable Digest Promotion Pipeline](../docs/src/3-agentops-and-lifecycle/index.md#from-dev-to-prd-immutable-digest-promotion-pipeline).
