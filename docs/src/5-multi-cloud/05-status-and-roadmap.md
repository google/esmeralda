# 5.5 Status & Roadmap

Multi-cloud support in Esmeralda is being delivered in small steps, each one deployed and verified end to end in a `dev` environment before it merges. Some building blocks from this section are on `main` today, some are in open pull requests, and some are planned. This page keeps the line between them explicit, so you can tell what you can deploy now from what is still arriving.

**Legend:** ✅ available on `main` · 🚧 in progress (open pull request or branch) · 🗺️ planned

---

## 1. Status by Building Block

### Identity

| Building Block | Status | Notes |
| :--- | :---: | :--- |
| SPIFFE Agent Identity per agent, verified by the Central Agent Gateway on every egress hop | ✅ | See [2.1 Central Agent Gateway](../3-agentops-and-lifecycle/01-central-agent-gateway.md). |
| Apigee partner authentication (`VerifyAPIKey`), API products and `owner_identity` app attribute | ✅ | See [1.4 Apigee X Partner Gateway](../2-workloads-and-catalog/04-apigee-partner-gateway.md). |
| Apigee `cross-cloud` capability unit: trusted-issuer KeyValueMap | ✅ | The KeyValueMap is managed; nothing on `main` reads it yet. |
| Cross-cloud shared flow: fail-closed OIDC verification (`401` / `403`), `iss#sub` principal, spoof-proof `x-verified-*` headers, key-to-identity binding | 🚧 | Open pull request (Apigee cross-cloud shared flow and partner chargeback). |
| `federated` and `spiffe` caller types in the catalog | ✅ | Declared and validated in the team-owned `catalog/`; only `service_account` callers and agents are compiled into IAM today. |
| Per-agent outbound identity (no shared invoker identity across agents) | 🗺️ | Prerequisite for verified per-agent attribution on every path. |
| Compile catalog callers into Apigee products, apps and `owner_identity` | 🗺️ | Today configured directly in Terraform ([5.4 Steps 3–4](./04-bringing-an-agent-from-another-cloud.md#step-3-trust-the-issuer-platform-security)). |
| Envoy cross-cloud path (JWT verification against an external issuer) | 🗺️ | The non-Apigee inbound alternative. |
| SPIFFE federation and mTLS callers, end to end | 🗺️ | The catalog shape exists; enforcement planned. |

### Connectivity & Protocols

| Building Block | Status | Notes |
| :--- | :---: | :--- |
| A2A between agents and MCP to tool servers over `*.esmeralda.internal` | ✅ | Framework-neutral; any cloud can speak them. |
| Central Agent Gateway + Agent Registry: deny-by-default egress to registered endpoints (`cross_cloud = "agent_gateway"`) | ✅ | External agents, MCP servers and HTTPS endpoints are registered as registry entries. |
| Apigee partner front door (`partner-agents`, `partner-mcp`) with `LLMTokenQuota` | ✅ | Optional; enabled with `governance.apigee.enabled`. |
| Apigee LLM proxy for agent → model calls | 🚧 | Branch in progress. |
| Reference hybrid networking (HA VPN / Cross-Cloud Interconnect to another cloud) | 🗺️ | Conceptual guidance in [5.2](./02-cross-cloud-connectivity.md#2-network-options); no Terraform module yet. |
| Registering `managed: false` external targets from the catalog into the Agent Registry | 🗺️ | Catalog shape exists; compile planned. |

### Observability & Chargeback

| Building Block | Status | Notes |
| :--- | :---: | :--- |
| Hub-and-spoke log sinks, OpenTelemetry baggage, `vw_monthly_agent_chargeback` | ✅ | Attribution from declared baggage. See [2.3](../3-agentops-and-lifecycle/03-centralized-monitoring-and-dashboards.md). |
| Apigee `ML-interaction` gateway records | ✅ | Partner app, product and token counts. |
| `esmeralda.usage` contract (`contracts/usage-event.schema.json`), validated in CI | 🚧 | Open pull request. |
| Python producers: ADK plugin, OTel GenAI span adapter, manual API, OTel logs transport | 🚧 | Same pull request as the contract. |
| Layer 4 usage analytics: usage table, normalized and billable views, schema-keyed metrics | 🚧 | Open pull request; the "unattributed" alert ships disabled until verified attribution lands. |
| `caller_identity` / `caller_issuer` in Apigee records and the partner chargeback view | 🚧 | Part of the cross-cloud shared flow pull request. |
| Verified attribution: principal → team table compiled from the catalog, `unmapped_caller: deny`, unattributed alarm on | 🗺️ | Turns chargeback from declared to verified. |
| Reference OpenTelemetry Collector configuration and GenAI-span-to-record processor for non-Python workloads on other clouds | 🗺️ | [5.3 §2](./03-unified-observability-and-chargeback.md#2-producers-on-every-cloud) shows a starting point. |
| Gateway records from Envoy in the `esmeralda.usage` shape | 🗺️ | JSON access-log template. |

### Catalog

| Building Block | Status | Notes |
| :--- | :---: | :--- |
| `catalog` map consumed by the Terraform units (targets, routes, probes, `callers` for agents and the test VM) | ✅ | Drives the entry point, IAM grants and `esmeralda verify`; `env.yaml` decodes it from `catalog/.resolved/<env>.json`. |
| Team-owned `catalog/` folder, `contracts/catalog.schema.json`, `esmeralda catalog resolve` / `show` | ✅ | Compiles to the same Terraform input as the former inline `env.yaml` block. See [2.6 Service Catalog](../3-agentops-and-lifecycle/06-service-catalog.md). |
| Additional catalog sources (Agent Registry inventory, project labels, enterprise adapters) | 🗺️ | Extension points are declared in `catalog/sources.yaml`. |

---

## 2. The Path from Here

The remaining work follows a deliberate order: first a normalized way to *describe* usage, then a single list of *who may call*, then *verified* attribution on top of both.

```mermaid
flowchart LR
    A["Usage contract<br/>+ producers 🚧"] --> B["Layer 4 usage<br/>analytics 🚧"]
    C["Team-owned<br/>catalog 🚧"] --> D["Verified attribution<br/>(fail closed) 🗺️"]
    B --> D
    E["Cross-cloud<br/>shared flow 🚧"] --> D
    D --> F["Catalog → Apigee<br/>products & apps 🗺️"]
    D --> G["Envoy cross-cloud<br/>path 🗺️"]
    B --> H["Reference OTel<br/>Collector 🗺️"]
```

1. **Describe usage the same way everywhere.** The `esmeralda.usage` contract and its producers, then the Layer 4 views that read only that contract.
2. **Know who may call.** The team-owned catalog, compiled into the same enforcement the platform already uses.
3. **Verify, then bill.** A principal-to-team table compiled from the catalog, `unmapped_caller: deny` as a property of the compile, and the "unattributed" alarm switched on.
4. **Widen the paths.** Catalog-driven Apigee products and apps, the Envoy cross-cloud alternative, and a reference collector for workloads in any language.

> [!TIP]
> Want to help? Each row marked 🗺️ is a self-contained contribution. See the **[Contributing Guide](../contributing.md)** for the workflow, and open an issue on [GitHub](https://github.com/google/esmeralda/issues) to discuss a design before you start.

---

**Next:** For every Make target and the contribution workflow, continue to **[6.1 Make Targets](../reference/make-targets.md)**.
