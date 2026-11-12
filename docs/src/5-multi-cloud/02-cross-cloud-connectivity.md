# 5.2 Cross-Cloud Connectivity & Protocols

In **[5.1 Identity Across Clouds](./01-identity-across-clouds.md)**, the underwriting agent on another cloud learned how to prove who it is. Its request still has to *get somewhere*: across a network boundary between two providers, through a gateway that can verify, authorize, meter and log it, and in a protocol both sides understand.

Esmeralda treats each of those as a separate, swappable decision:

| Decision | Options | Esmeralda Default |
| :--- | :--- | :--- |
| **Which gateway role on the hop?** | Apigee, Envoy, Agent Gateway, `direct` | Apigee for inbound cross-cloud calls when it is enabled; Agent Gateway for all agent egress |
| **Which network path?** | Public endpoint with TLS + identity, HA VPN, Cross-Cloud Interconnect | Whatever the organization already runs; identity is verified the same way on every path |
| **Which protocol?** | A2A for agent-to-agent, MCP for agent-to-tool | A2A and MCP over HTTPS |

---

## 1. The Gateway per Hop

Which gateway handles each hop, for every hop in the platform, is decided in one place: **[1.5 One Gateway per Hop](../2-workloads-and-catalog/05-gateway-per-hop.md)**. For calls that cross a cloud boundary, it comes down to two hops:

| Hop | Default | Without Apigee | Status |
| :--- | :--- | :--- | :---: |
| Agent or MCP client on another cloud → Esmeralda agent or MCP server (inbound) | Apigee partner proxies + cross-cloud shared flow (caller verification, per-product `allowed_agents` / `allowed_tools`, `LLMTokenQuota`, verified `caller_identity` in the gateway record) | Envoy with JWT verification against the external issuer (🗺️), or `direct` with Workload Identity Federation and IAM only (no gateway record) | 🚧 |
| Esmeralda agent → agent, MCP server or API on another cloud (outbound) | Central Agent Gateway + Agent Registry | Same: the Agent Gateway is always on | ✅ |

The identity side of the inbound hop is in [5.1 §3](./01-identity-across-clouds.md#3-verifying-an-oidc-caller-at-apigee-the-cross-cloud-shared-flow), and the Apigee deep dive (PSC plumbing, products, quota phases) is in **[1.4 Partner API Management & Token Monetization with Apigee X](../2-workloads-and-catalog/04-apigee-partner-gateway.md)**.

### Registering Remote Resources for Outbound Calls

When an Esmeralda agent calls something on another cloud, the **Central Agent Gateway** is already on the path. Register the remote endpoint in the **Agent Registry**, which accepts agents, MCP servers and plain HTTPS endpoints hosted outside Google Cloud:

| Remote Resource | Registry Entry |
| :--- | :--- |
| A2A agent on another cloud | An agent entry with its A2A agent card |
| MCP server on another cloud | An MCP server entry with its tool specification |
| Any other HTTPS API (including the remote cloud's token-exchange endpoint) | An endpoint entry with its URL |

The gateway matches **exact hostnames**, so register every hostname variant the agent will use. Anything unregistered is denied by default, which is exactly the property that makes outbound cross-cloud calls auditable. See **[2.1 Zero-Trust Egress & The Central Agent Gateway](../3-agentops-and-lifecycle/01-central-agent-gateway.md)** for how per-target IAP authorization and the internal Root CA work.

---

## 2. Network Options

Identity is verified the same way on every network path, so the network choice is about latency, cost, privacy and what the organization already operates, not about trust. Three options cover most estates:

| Option | What It Is | When to Choose It | What to Plan For |
| :--- | :--- | :--- | :--- |
| **Public endpoints with TLS + identity** | The caller reaches a public HTTPS endpoint; every request carries a verified token. | Fastest start, low volume, partners you do not share a network with. | Expose the inbound gateway publicly (for example, Apigee behind an external load balancer); outbound calls from the Agent Gateway leave from shared Google addresses unless egress is routed through Cloud NAT with reserved IPs. |
| **HA VPN** | Encrypted IPsec tunnels between the Shared VPC and the other cloud's network. | Private connectivity at moderate bandwidth. | Advertise the relevant subnets on both sides, extend private DNS (for example, forwarding zones for the other cloud's private domains), and trust the other side's private CA if it uses one. |
| **Cross-Cloud Interconnect** | A dedicated physical connection between Google Cloud and another provider. | High, steady volume and predictable latency. | The same routing, DNS and certificate work as HA VPN, at higher capacity. |

```mermaid
flowchart LR
    subgraph OC["Other cloud network"]
        UW["Underwriting agent"]
    end
    subgraph GCP["Esmeralda Shared VPC"]
        AX["Inbound gateway<br/>(Apigee / Envoy)"]
        AGW["Central Agent Gateway<br/>(network attachment)"]
    end

    UW <-->|"Option 1: public HTTPS + verified token"| AX
    UW <-->|"Option 2: HA VPN"| GCP
    UW <-->|"Option 3: Cross-Cloud Interconnect"| GCP
```

On the private options, the Agent Gateway reaches private destinations through its network attachment into the Shared VPC; from there, normal VPC routes carry traffic over the VPN or Interconnect. The other cloud needs a return route to that attachment subnet.

> [!WARNING]
> **Plan end-to-end mTLS carefully.** The Agent Gateway terminates and re-originates TLS so it can inspect and authorize each request (agents trust it through the gateway root certificates that Esmeralda injects at deploy time). A client certificate held by an agent therefore does not reach the remote backend. If the remote side requires mTLS, terminate it at a proxy on the remote side and keep identity at L7 with signed tokens. The same applies inbound: terminate client certificates at the gateway, not inside agent code.

---

## 3. A2A and MCP: The Interoperable Protocols

A gateway and a network path are only useful if both sides speak the same language. Esmeralda standardizes on two open protocols that work regardless of cloud or framework:

| Protocol | Used For | What Esmeralda Exposes | Spec |
| :--- | :--- | :--- | :--- |
| **A2A (Agent2Agent)** | Agent-to-agent delegation: discovery through an agent card, then task messages | Agent card at `https://<agent>.esmeralda.internal/v1/card`; messages at `/v1/message:send` and `/v1/message:stream` | [a2a-protocol.org](https://a2a-protocol.org/) |
| **MCP (Model Context Protocol)** | Agent-to-tool calls: listing and calling tools | `https://<server>.esmeralda.internal/mcp` (Streamable HTTP) | [modelcontextprotocol.io](https://modelcontextprotocol.io/) |

Because both are plain HTTPS with JSON payloads, an agent written in LangGraph on another cloud can call `ai-coe-mortgage-specialist` exactly the way `cx-mortgage-orchestrator` does, and an Esmeralda agent can call an MCP server written in any language anywhere. Neither side needs to know the other's runtime, framework or cloud.

Two supporting standards keep the conversation observable and governable across the boundary:

* **W3C Trace Context (`traceparent`)** travels on every A2A and MCP request through standard OpenTelemetry HTTP instrumentation, so a request that starts on another cloud and ends in an Esmeralda MCP server shares one `trace_id`. That id is also the join key for usage records ([5.3](./03-unified-observability-and-chargeback.md)).
* **Agent cards and tool specifications** registered in the Agent Registry make remote agents and tools discoverable and governable, just like local ones.

> [!TIP]
> Content inspection on the gateway (Model Armor) covers specific protocol operations, and coverage differs between protocol versions and between streaming and non-streaming calls. When a cross-cloud call must be inspected, check the [Model Armor integration documentation](https://cloud.google.com/model-armor/docs) for the operations it covers and prefer a non-streaming call shape for that hop.

---

**Next Subsection:** The request is verified, routed and understood. How do the CFO and the FinOps team see what it cost when half of the work happened on another cloud? Continue to **[5.3 Unified Observability & Chargeback](./03-unified-observability-and-chargeback.md)**.
