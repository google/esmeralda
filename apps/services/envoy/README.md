# Envoy Gateway Service

**Envoy** is the reverse proxy behind every `*.esmeralda.internal` hostname. When an agent calls `https://legacy-dms.esmeralda.internal/mcp` or `https://ai-coe-mortgage-specialist.esmeralda.internal`, the request reaches Envoy after the internal HTTPS load balancer and Cloud Run IAM. Envoy then:

1. routes it by Host header to the right Cloud Run MCP server or Reasoning Engine;
2. rewrites the upstream host and path;
3. replaces `Authorization` with a Google token for the gateway's own service account.

That token is an **OIDC ID token** for Cloud Run upstreams and an **OAuth2 access token** for `*.googleapis.com`. It comes from Envoy's built-in [`gcp_authn`](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/gcp_authn_filter) filter and is cached until it expires. Envoy also rate-limits per `X-API-Key`.

This directory holds only the image. It replaced the Kong image and its Lua token plugin (issue #73).

## Contents

| File | Purpose |
| :--- | :--- |
| [`Dockerfile`](./Dockerfile) | `envoyproxy/envoy` distroless 1.40.2, pinned by digest. No custom code and no plugins. |

The image contains **no routes and no certificates**: it is environment-neutral and promoted by digest.

## How it is wired (Layer 5)

[`infrastructure/modules/5-workloads/services/envoy`](../../../infrastructure/modules/5-workloads/services/envoy/main.tf) deploys:

- **The Envoy bootstrap.** The pure [`config`](../../../infrastructure/modules/5-workloads/services/envoy/config/main.tf) submodule renders it from `agent_endpoints`. It's stored in Secret Manager (`envoy-config-<env>`) and mounted at `/etc/envoy/envoy.yaml`; each config change rolls out a new revision.
- **The Cloud Run service** `envoy-gateway-<env>`:
  - `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` ingress;
  - custom audiences for every served hostname;
  - a request timeout of 3600 s, for streams.
- **A regional internal HTTPS load balancer**:
  - a static VIP;
  - a leaf certificate signed by the layer-3 internal Root CA;
  - the `esmeralda.internal` and `*.esmeralda.internal` A records.

## Config changes and rollback

- **Every config change makes a new secret version and a new revision.**
  - Each revision mounts the exact secret version it was deployed with.
  - Old versions are kept (`deletion_policy = "ABANDON"`), so earlier revisions can still start instances.
- **To roll back,** send traffic to the previous revision:

  ```bash
  gcloud run services update-traffic envoy-gateway-<env> --to-revisions=<previous-revision>=100 \
    --project=<gateway-project> --region=<region>
  ```

  Then revert the change in Terraform.
- **Housekeeping:** old secret versions accumulate. Destroy the ones no revision uses (`gcloud secrets versions destroy`).

## Testing

- `make test-gateway`: renders the config module and runs it in a real `envoy` against a fake metadata server and TLS mock upstreams ([`tests/gateway`](../../../tests/gateway)).
- [`tests/gateway/sandbox`](../../../tests/gateway/sandbox/README.md): a throwaway GCP project that runs the module for real, behind the internal LB, and checks it from a VM.
