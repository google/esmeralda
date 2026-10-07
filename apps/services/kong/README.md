# Kong API Gateway Service

**Kong Gateway** is an open-source API gateway: a reverse proxy that routes incoming HTTP requests to upstream services based on rules (hostnames, paths) and applies plugins (authentication, rate limiting, ...) on the way.

In Esmeralda, Kong is the single private entry point behind every `*.esmeralda.internal` hostname. When an agent calls `https://legacy-dms.esmeralda.internal/mcp` or `https://ai-coe-mortgage-specialist.esmeralda.internal`, the request (after passing the [Central Agent Gateway](../../../docs/3-agentops-and-lifecycle/01-central-agent-gateway.md)) reaches Kong, which forwards it to the right Cloud Run MCP server or Reasoning Engine with a Google credential attached. This is what lets the CX team's orchestrator call the AI CoE team's reusable specialist through a stable URL, regardless of the engine's real Vertex AI endpoint.

This directory contains the container packaging and runtime configuration for Kong running as a serverless **Cloud Run v2** service in the gateway project (`esm-<env>-gateway-<sfx>`).

## Contents

| File | Purpose |
| :--- | :--- |
| [`Dockerfile`](./Dockerfile) | Builds on `kong:3.7-ubuntu` and installs the custom plugin. |
| [`kong.conf.default`](./kong.conf.default) | DB-less mode, declarative config at `/etc/kong/kong.yml`, proxy on `:8000`, Admin API off. |
| [`plugins/gcp-service-account/`](./plugins/gcp-service-account/) | Custom plugin that fetches a token from the Cloud Run metadata server (as `sa-esmeralda-kong-<env>`) and adds it to the upstream request: an **OAuth2 access token** when the audience is a `googleapis.com` endpoint (Reasoning Engines), an **OIDC ID token** for the given audience otherwise (Cloud Run MCP servers). |

The image contains **no routes and no certificates**; it is environment-neutral and promoted by digest like every other image.

## How it is wired (Layer 5)

The Terraform module [`infrastructure/modules/5-workloads/services/kong`](../../../infrastructure/modules/5-workloads/services/kong/main.tf) deploys:

- **Declarative routes**: rendered from [`templates/kong.yml.tpl`](../../../infrastructure/modules/5-workloads/services/kong/templates/kong.yml.tpl) using the engine IDs / service URLs of the MCP servers and agents, stored in Secret Manager (`kong-config-<env>`) and mounted at `/etc/kong/kong.yml`. Each upstream gets a Host route (`<name>.esmeralda.internal`), path routes (`/<name>`, `/agents/<name>`) and the `gcp-service-account` plugin. A global `rate-limiting` plugin is also applied.
- **Cloud Run service** `kong-gateway-<env>` with `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` ingress.
- **Regional internal HTTPS load balancer** in the Shared VPC whose `*.esmeralda.internal` leaf certificate is signed by the internal Root CA from Layer 3, plus the `esmeralda.internal` / `*.esmeralda.internal` A records in the private DNS zone. See [01. Central Agent Gateway §3.10](../../../docs/3-agentops-and-lifecycle/01-central-agent-gateway.md#310-our-private-pki-internal-root-ca-and-kongs-leaf-certificate).

## Build & Deployment

```bash
make build-service-kong          # or: make build-services (all MCP images + Kong)
make deploy-gateway ENV=dev      # applies infrastructure/live/<env>/layer-5-workloads/services/kong
```

- The image is built by Cloud Build in the shared CI/CD project and pushed as `kong-gateway` to the dev repository; prd pulls the promoted tag from the release repository.
- `make deploy-workloads` deploys Kong **after** the MCP services and agents, because routes embed their engine IDs. **Re-run `make deploy-gateway` whenever an agent is recreated.**
- `make deploy-services` deploys only the three MCP services, **not** Kong.
