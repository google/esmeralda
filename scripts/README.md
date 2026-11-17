# `scripts/`

Shell helpers that the `esmeralda` CLI and a few Make targets still call. This folder is shrinking:

> **New logic goes into the `esmeralda` CLI** (`packages/esmeralda/src/esmeralda/ops/`, with a thin
> Make alias if needed), not into a new script here. The only code that has to stay shell is what
> runs **on the test VM** over IAP SSH, where there is no checkout or virtual environment.
> Static checks go into `tests/guards/` as pytest (for example `tests/guards/test_image_pinning.py`), and
> `tests/guards/test_scripts_dir.py` keeps this folder to an allowlist.

Terraform `local-exec` helpers are not here: they live next to the modules in
`infrastructure/modules/_shared/scripts/`.

| File | What it does | Called by |
|---|---|---|
| `check_lock.sh` | `uv lock --check`, offline, against the public index | `make lock-check`, `.githooks/pre-push`, `uv_lock.sh` |
| `uv_lock.sh` | Regenerates `uv.lock` in Cloud Build against public PyPI and copies it back | `make lock [PKG=…]` |
| `preflight.sh` | Workstation tool, credential and billing checklist | `make preflight` (and `make bootstrap`) |
| `vm/gateway_probes.sh` | Runs **on the test VM**: probes every catalog host through the entry point, plus the verified attribution probes (the CLI resolves the inputs in Python, `esmeralda.ops.testvm`) | `esmeralda verify` (and its MCP tool), `make test-gateway` |
| `test_partner.sh` | Apigee partner front-door conformance checks (offline bundle checks, or live probes from the VM) | `esmeralda verify --partner` (`make test-partner`) |
| `partner_env.sh` | Sourced helper: resolves the test VM, entry point, internal CA and partner key | `test_partner.sh`, `partner_load_test.sh` |
| `partner_load_test.sh` | Runs the partner Locust load test on the test VM and copies the reports back | `make load-test-partner` |
