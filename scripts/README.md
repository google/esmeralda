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
| `vm/gateway_probes.sh` | Runs **on the test VM**: probes every catalog host through the entry point, plus the verified attribution probes (the CLI resolves the inputs in Python, `esmeralda.ops.testvm`) | `esmeralda verify` (and its MCP tool), `make test-gateway` |
| `vm/partner_load.sh` | Runs **on the test VM**: installs Locust (cached venv) and runs the partner load test headless (the CLI resolves the inputs and copies the reports back, `esmeralda.ops.partner_load`) | `make load-test-partner` |
