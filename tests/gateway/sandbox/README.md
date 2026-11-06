# Gateway sandbox

A throwaway GCP project that runs [`services/envoy`](../../../infrastructure/modules/5-workloads/services/envoy/main.tf) for real, so the gateway can be checked end to end before any environment uses it (issue #73). It complements the [local harness](../) (`make test-gateway`), which runs the same Envoy config against fakes.

## What it deploys

| Piece | Why |
| :--- | :--- |
| Project in the folder you pass, linked to your billing account | Isolation; destroyed when you're done |
| VPC with a core subnet (Private Google Access) and a proxy-only subnet | Cloud Run Direct VPC egress and the regional internal HTTPS LB |
| Private zone `esmeralda.internal` | The hostnames callers use |
| Self-signed root CA | Signs the LB certificate, as layer 3 does |
| `gateway-upstream` on Cloud Run (internal ingress, IAM) | Stands in for an MCP server and a streaming agent ([upstream.py](upstream.py) on `python:3.12-slim`, no build) |
| `services/envoy` | The gateway under test: Envoy on Cloud Run, Secret Manager config, internal HTTPS LB, static VIP, DNS records |
| `test-vm-sbx` (e2-micro, no external IP, IAP SSH) | The caller: mints ID tokens from its metadata server and trusts the sandbox CA |

The gateway serves three targets that mirror the real ones:
- `legacy-dms` → the upstream, with an ID token;
- `ai-coe-mortgage-specialist` → Vertex AI (`…/locations/<region>/reasoningEngines`), with an OAuth2 access token;
- `cx-mortgage-orchestrator` → the upstream's SSE stream, through the `/api/stream_reasoning_engine` path mapping.

## Use it

Prerequisites:
- `gcloud`, logged in, with Application Default Credentials;
- OpenTofu or Terraform 1.6+;
- permission to create projects in the folder.

```bash
cd tests/gateway/sandbox
export TF_VAR_billing_account=XXXXXX-XXXXXX-XXXXXX TF_VAR_folder_id=123456789012

# State holds the sandbox CA key: keep it outside the repository. (`make clean` also deletes every
# *.tfstate inside the repo, and a lost state leaves the project running and billing.)
tofu init -backend-config="path=$HOME/.esmeralda/gateway-sandbox.tfstate"
tofu apply                 # ~5–10 minutes
./run_checks.sh            # runs checks.sh on the test VM over IAP SSH
tofu destroy               # deletes the project (it stays recoverable for 30 days)
```

## What `checks.sh` verifies

All calls take the production path: private DNS, then the internal HTTPS LB (private-CA TLS), then Cloud Run IAM, then Envoy, then the upstream.

1. Every `*.esmeralda.internal` name, and the apex, resolves to the LB VIP.
2. **MCP route:**
   - the path is kept;
   - `X-API-Key` and `User-Auth-Token` pass through;
   - the caller's token is replaced by an ID token whose audience is the upstream's Cloud Run URL.
3. Requests with no token, or with a token for another audience, are rejected by Cloud Run IAM.
4. **Vertex AI route:** a real OAuth2 access token minted by `gcp_authn` is accepted by `aiplatform` (200).
5. A 20 s blocking call succeeds, even though Envoy's default route timeout is 15 s.
6. SSE events arrive as they're produced, and the stream lasts more than 15 s.
7. The apex path route strips its prefix, and `/healthz` answers `ok`.
8. An unknown host, called with a valid token, gets 404 from Envoy.
9. Request `rate_limit_per_minute + 1` with the same `X-API-Key` gets 429. The sandbox runs one instance, so the limit is exact.

## Cost and limits

- **First apply on a brand-new project:** Cloud Run can fail the gateway service with `Error code 7 … internal error`. That happened on 2026-11-06: the same revision became Ready about 10 s later on its own, which points to IAM propagation. Run `tofu untaint module.gateway.google_cloud_run_v2_service.gateway` and apply again.
- **A new internal LB needs a few minutes before TLS works everywhere.** Until it has programmed all its proxies, handshakes fail intermittently (`sslv3 alert handshake failure`). `checks.sh` waits until `/healthz` answers 5 times in a row.
- **Cost:** about $1–2 per day while it exists. Most of it is the LB forwarding rule and the e2-micro VM; Cloud Run scales to zero. Destroy it when you're done.
- **Images:** Docker Hub, pinned by digest. Production builds `apps/services/envoy` into Artifact Registry and promotes it by digest.
- **Not covered:** the Agent Gateway leg (PSC network attachment, TrustConfig) and the real Agent Engine agents and MCP servers. Those need the full stack (`make deploy-all`).
