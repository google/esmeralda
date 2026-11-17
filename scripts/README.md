# `scripts/`

Shell helpers that the `esmeralda` CLI and a few Make targets still call. This folder is shrinking:

> **New logic goes into the `esmeralda` CLI** (`packages/esmeralda/src/esmeralda/ops/`, with a thin
> Make alias if needed), not into a new script here. The only code that has to stay shell is what
> runs **on the test VM** over IAP SSH, where there is no checkout or virtual environment.
> Static checks go into `tests/guards/` as pytest.

Terraform `local-exec` helpers are not here: they live next to the modules in
`infrastructure/modules/_shared/scripts/`.

| File | What it does | Called by |
|---|---|---|
| `check_image_pinning.sh` | Fails if a Layer 5 workload deploys an image from a tag variable instead of a digest | `make test-terraform` (so `make test-all` and the agent build prerequisites) |
| `check_lock.sh` | `uv lock --check`, offline, against the public index | `make lock-check`, `.githooks/pre-push`, `uv_lock.sh` |
| `uv_lock.sh` | Regenerates `uv.lock` in Cloud Build against public PyPI and copies it back | `make lock [PKG=…]` |
| `preflight.sh` | Workstation tool, credential and billing checklist | `make preflight` (and `make bootstrap`) |
| `with_local_mcp.sh` | Starts the local MCP servers around a command, then stops them | `make serve` |
| `promote_release.sh` | `status` / `promote`: copies a verified dev build by digest into the release repository and writes `release/<env>.yaml` (see `release/README.md`) | `esmeralda release status\|promote` (`make status-release`, `make promote`) |
| `release_manifest.py` | Stdlib writer/reader of `release/<env>.yaml` | `promote_release.sh`, `verify_images.sh` |
| `tests/test_release_manifest.py` | Unit tests for `release_manifest.py` | `make test-release` (part of `make test`) |
| `verify_images.sh` | Compares the digest every Layer 5 workload runs with the release manifest (or its tag) | `esmeralda build --verify` (`make verify-images`) and the end of every `esmeralda deploy --layer 5` |
| `test_gateway.sh` | Resolves the test VM, entry point and internal CA, then probes every catalog host from the VM | `esmeralda verify` (and its MCP tool), `make test-gateway` |
| `test_partner.sh` | Apigee partner front-door conformance checks (offline bundle checks, or live probes from the VM) | `esmeralda verify --partner` (`make test-partner`) |
| `partner_env.sh` | Sourced helper: resolves the test VM, entry point, internal CA and partner key | `test_partner.sh`, `partner_load_test.sh` |
| `partner_load_test.sh` | Runs the partner Locust load test on the test VM and copies the reports back | `make load-test-partner` |
