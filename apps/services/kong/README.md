# Kong API Gateway Service

**Kong Gateway** is an open-source API gateway: a reverse proxy that routes incoming HTTP requests to upstream services based on rules (hostnames, paths) and applies plugins (authentication, rate limiting, ...) on the way.

In Esmeralda, Kong is the default gateway behind the private entry point (an internal HTTPS load balancer) that serves every `*.esmeralda.internal` hostname. When an agent calls `https://legacy-dms.esmeralda.internal/mcp` or `https://ai-coe-mortgage-specialist.esmeralda.internal`, the request (after passing the [Central Agent Gateway](../../../docs/src/3-agentops-and-lifecycle/01-central-agent-gateway.md)) reaches the entry point and then Kong, which forwards it to the right Cloud Run MCP server or Reasoning Engine with a Google credential attached. This is what lets the CX team's orchestrator call the AI CoE team's reusable specialist through a stable URL, regardless of the engine's real Vertex AI endpoint.

This directory contains the container packaging and runtime configuration for Kong running as a serverless **Cloud Run v2** service in the gateway project (`esm-<env>-gateway-<sfx>`).

## Contents

| File | Purpose |
| :--- | :--- |
| [`Dockerfile`](./Dockerfile) | Builds on `kong:3.7-ubuntu` and installs the custom plugin. |
| [`kong.conf.default`](./kong.conf.default) | DB-less mode, declarative config at `/etc/kong/kong.yml`, proxy on `:8000`, Admin API off. |
| [`plugins/gcp-service-account/`](./plugins/gcp-service-account/) | Custom plugin that fetches a token from the Cloud Run metadata server (as `sa-esmeralda-kong-<env>`) and adds it to the upstream request: an **OAuth2 access token** when the audience is a `googleapis.com` endpoint (Reasoning Engines), an **OIDC ID token** for the given audience otherwise (Cloud Run MCP servers). |

The image contains **no routes and no certificates**; it is environment-neutral and promoted by digest like every other image.

## How it is wired (Layer 5)

The Terraform module [`infrastructure/modules/5-workloads/gateways/kong`](../../../infrastructure/modules/5-workloads/gateways/kong/main.tf) deploys:

- **Declarative routes**: rendered from [`templates/kong.yml.tpl`](../../../infrastructure/modules/5-workloads/gateways/kong/templates/kong.yml.tpl) using the engine IDs / service URLs of the MCP servers and agents, stored in Secret Manager (`kong-config-<env>`) and mounted at `/etc/kong/kong.yml`. Each upstream gets a Host route (`<name>.esmeralda.internal`), path routes (`/<name>`, `/agents/<name>`) and the `gcp-service-account` plugin. A global `rate-limiting` plugin is also applied.
- **Cloud Run service** `kong-gateway-<env>` with `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` ingress.
- **Serverless NEG + regional `INTERNAL_MANAGED` backend service**, exposed as the `backend_service_id` output (the gateway contract), plus a `routes` output listing every host and how `make test-gateway` probes it.

Kong owns no load balancer, certificate or DNS record. Those belong to the **entry point**: the reserved IP and the `esmeralda.internal` / `*.esmeralda.internal` A records (Layer 2), the leaf certificate signed by the internal Root CA (Layer 3), and the URL map / HTTPS proxy / forwarding rule ([`infrastructure/modules/5-workloads/entrypoint`](../../../infrastructure/modules/5-workloads/entrypoint/main.tf), Layer 5). See [Ingress Gateways](../../../docs/src/2-workloads-and-catalog/01-ingress-gateways.md) and [01. Central Agent Gateway §3.10](../../../docs/src/3-agentops-and-lifecycle/01-central-agent-gateway.md#310-our-private-pki-internal-root-ca-and-the-entry-points-leaf-certificate).

## Build & Deployment

```bash
make build-service-kong          # or: make build-services (all MCP images + Kong)
make deploy-gateway ENV=dev      # applies infrastructure/live/<env>/layer-5-workloads/gateways/kong
make deploy-entrypoint ENV=dev   # applies infrastructure/live/<env>/layer-5-workloads/entrypoint (routes hosts to Kong)
make test-gateway ENV=dev        # probes every Kong route through the entry point from the test VM
```

- The image is built by Cloud Build in the shared CI/CD project and pushed as `kong-gateway` to the dev repository; prd pulls the promoted tag from the release repository.
- `make deploy-workloads` deploys Kong **after** the MCP services and agents, because routes embed their engine IDs, and the entry point after Kong. **Re-run `make deploy-gateway` whenever an agent is recreated.**
- `make deploy-services` deploys only the three MCP services, **not** Kong.
