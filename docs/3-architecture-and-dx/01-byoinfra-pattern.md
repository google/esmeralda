# Deployment Strategy: Greenfield vs. Brownfield (BYOInfra)

## 🔌 2. Greenfield vs. Brownfield (BYOInfra) Toggle Design

In real corporate production environments, network and security teams (NetOps and SecOps) rarely allow Terraform scripts to create Shared VPC networks, subnets, internet DNS zones, or billing projects from scratch. Enterprise clients require deploying workloads onto pre-existing resources (**Brownfield** / **BYOInfra**).

Esmeralda solves this constraint by implementing dynamic toggle switches in its Terragrunt environment configs (`live/dev/env.yaml`), utilizing native `skip` parameters and conditional fallbacks:

To allow seamless deployment inside enterprise client environments with pre-existing resources, the architecture implements the **BYOInfra Pattern** natively using Terragrunt's skip parameters and input-fallbacks:

### A. The Client's Environment Parameters (`live/client-prod/env.yaml`)
The client declares their pre-existing resources and toggles dynamic skip flags (`true`):

> [!TIP]
> 📁 **Client Environment Parameters File Available:**
> The static bypass settings and corporate network pointers for enterprise client environments are available at:
> 👉 [`env.yaml`](../migration/02_workloads_and_delivery/infrastructure/live/client-prod/env.yaml)


### B. Dynamically Skipping Stage 2 (`live/client-prod/stage-2-networking/terragrunt.hcl`)
Using Terragrunt's native skip block, the networking stage skips compilation and returns instantly if `byo_networking` is active:

> [!TIP]
> 📁 **Live Terragrunt Configuration Available:**
> The live Terragrunt file implementing infrastructure skip logic in client environments is available at:
> 👉 [`terragrunt.hcl`](../migration/02_workloads_and_delivery/infrastructure/live/client-prod/stage-2-networking/terragrunt.hcl)


### C. Downstream Fallback Lookup (`live/client-prod/stage-4-workloads/agents/a2a-agent/terragrunt.hcl`)
Workloads consuming outputs from skipped stages dynamically switch their inputs to read from the static fields in `env.yaml`, preventing Terragrunt parser evaluation errors:

> [!TIP]
> 📁 **Live Terragrunt Configuration Available:**
> The live Terragrunt configuration implementing dynamic fallback to client static data for the A2A Agent is available at:
> 👉 [`terragrunt.hcl`](../migration/02_workloads_and_delivery/infrastructure/live/client-prod/stage-4-workloads/agents/a2a-agent/terragrunt.hcl)
