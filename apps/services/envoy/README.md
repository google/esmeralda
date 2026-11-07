# Envoy Gateway Service

**Envoy** is the optional, bring-your-own reverse proxy (adapter) behind the entry point. By default every `*.esmeralda.internal` host is served by the built-in `direct` backend, with no proxy. When the entry point routes a host to `envoy` (`gateways` in `env.yaml`), a call to e.g. `https://legacy-dms.esmeralda.internal/mcp` or `https://ai-coe-mortgage-specialist.esmeralda.internal` reaches Envoy after the entry point (internal HTTPS load balancer) and Cloud Run IAM, so callers send an ID token for every host routed to it. Envoy then:

1. routes it by Host header to the right Cloud Run MCP server or Agent Runtime engine;
2. rewrites the upstream host and path;
3. replaces `Authorization` with a Google token for the gateway's own service account.

That token is an **OIDC ID token** for Cloud Run upstreams and an **OAuth2 access token** for `*.googleapis.com`. It comes from Envoy's built-in [`gcp_authn`](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/gcp_authn_filter) filter and is cached until it expires. Envoy also rate-limits per `X-API-Key`.

This directory holds only the image. Envoy is the reference adapter (issue #73): it needs no custom image code or token plugin.

## Contents

| File | Purpose |
| :--- | :--- |
| [`Dockerfile`](./Dockerfile) | `envoyproxy/envoy` distroless 1.40.2, pinned by digest. No custom code and no plugins. |

The image contains **no routes and no certificates**: it is environment-neutral and promoted by digest.

## How it is wired (Layer 5)

[`infrastructure/modules/5-workloads/gateways/envoy`](../../../infrastructure/modules/5-workloads/gateways/envoy/main.tf) deploys:

- **The Envoy bootstrap.** The pure [`config`](../../../infrastructure/modules/5-workloads/gateways/envoy/config/main.tf) submodule renders it from `agent_endpoints` (derived from the service catalog in `env.yaml`). It's stored in Secret Manager (`envoy-config-<env>`) and mounted at `/etc/envoy/envoy.yaml`; each config change rolls out a new revision.
- **The Cloud Run service** `envoy-gateway-<env>`:
  - `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` ingress;
  - custom audiences for every served hostname;
  - a request timeout of 3600 s, for streams.
- **The gateway contract**: a serverless NEG and a regional `INTERNAL_MANAGED` backend service (output `backend_service_id`), plus the `routes` output `make test-gateway` probes. The load balancer, certificate, VIP and DNS records belong to the entry point (layers 2, 3 and the layer-5 `entrypoint` unit), not to this module.

Enable it with `envoy` in `gateways.enabled` (and `gateways.default` or `gateways.host_rules`) in `env.yaml`; see [Entry point and gateways](../../../docs/src/2-workloads-and-catalog/01-ingress-gateways.md).

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

- `make test-gateway-unit`: renders the config module and runs it in a real `envoy` against a fake metadata server and TLS mock upstreams ([`tests/gateway`](../../../tests/gateway)).
- [`tests/gateway/sandbox`](../../../tests/gateway/sandbox/README.md): a throwaway GCP project that runs the module for real, behind the internal LB, and checks it from a VM.
