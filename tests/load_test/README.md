
# Robust Load Testing for Generative AI Applications

This directory provides a load testing framework for Esmeralda's deployed agents, built on [Locust](https://locust.io), a leading open-source load testing tool: you describe a simulated user's behaviour in Python, and Locust runs many of those users concurrently while recording latencies and failures.

[`load_test.py`](./load_test.py) simulates multi-turn users against a **Vertex AI Agent Engine (Reasoning Engine)**, typically the CX team's `cx-mortgage-orchestrator`. Each simulated user:

- creates a session (`class_method: async_create_session` on `:query`), then sends 1 to 5 turns (`async_stream_query` on `:streamQuery?alt=sse`) before rotating to a new session;
- reports **Time to First Event (TTFE)**, per-MCP-tool latency and total turn latency, parsed from the SSE stream;
- waits 10 to 20 s between turns and tracks a rolling 60 s request rate, logging a warning at 80 RPM and an error at 90 RPM (the regional Reasoning Engine quota assumed by the script).

The same script runs in the staging Cloud Build pipeline ([`.cloudbuild/staging.yaml`](../../.cloudbuild/staging.yaml)), which uploads the results to GCS.

## Load Testing

Before running load tests, make sure the target environment is deployed (see the root [README](../../README.md)), e.g. `make deploy-all ENV=dev`, and that `make test-e2e ENV=dev` passes.

Follow these steps to execute load tests:

**1. Install dependencies:**
   Locust is part of the workspace dependencies, so the standard bootstrap is enough:

   ```bash
   make bootstrap
   ```

**2. Point the test at the deployed orchestrator:**
   The script reads the engine's full resource name (`projects/<project>/locations/<region>/reasoningEngines/<id>`) from `REMOTE_AGENT_ENGINE_ID`, or from a `REMOTE_AGENT_ENGINE_ID=` line in the repository's root `.env`. Get it from the Layer 5 Terragrunt output:

   ```bash
   cd infrastructure/live/dev/layer-5-workloads/agents/cx-mortgage-orchestrator
   terragrunt output -raw engine_id   # copy the value
   cd -
   export REMOTE_AGENT_ENGINE_ID=projects/esm-dev-cx-agents-<sfx>/locations/us-central1/reasoningEngines/<id>
   ```

   > If neither is set, the script falls back to a placeholder engine ID and every request fails.

**3. Execute the Load Test:**
   Authentication uses Application Default Credentials (`gcloud auth application-default login`), falling back to `gcloud auth print-access-token`, then to the `_AUTH_TOKEN` env var. Trigger the test with:

   ```bash
   uv run locust -f tests/load_test/load_test.py \
   --headless \
   -t 30s -u 5 -r 2 \
   --csv=tests/load_test/.results/results \
   --html=tests/load_test/.results/report.html
   ```

   This command runs a 30-second load test that spawns 2 users per second up to a maximum of 5 concurrent users. Results are written to `tests/load_test/.results/` (gitignored).

## Alternative: `make load-test-cx-mortgage-orchestrator`

The Makefile target runs a separate, simpler multi-turn script, [`apps/agents/cx-mortgage-orchestrator/scripts/locustfile.py`](../../apps/agents/cx-mortgage-orchestrator/scripts/locustfile.py) (5 users, 1 minute, fixed mortgage conversation flows). It reads `CX_AGENTS_PROJECT_ID` and `ROOT_REASONING_ENGINE_ID` (the numeric engine ID only), which you must export yourself; the target does not resolve them from Terragrunt:

```bash
export CX_AGENTS_PROJECT_ID=esm-dev-cx-agents-<sfx>
export ROOT_REASONING_ENGINE_ID=<id>
make load-test-cx-mortgage-orchestrator
```
