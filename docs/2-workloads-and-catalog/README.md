# Workloads & Catalog (Stage 4)

This section of the documentation unifies all specifications for Esmeralda's Stage 4 workloads (Swappable Ingress Gateways, Standalone API Hub, Composable MCP Server Tools, and Atomic AI Agents with Database Bootstrapping).

### The Composable AI Workloads Matrix

Stage 4 operates as an independent product shelf: operators select an ingress gateway adapter, deploy reusable MCP tool servers, and assemble reasoning engines onto the foundational projects:

```mermaid
flowchart TB
    subgraph Gateways["Ingress Adapters (modules/4-workloads/gateways)"]
        G1["Apigee X Enterprise Gateway"]
        G2["Kong DB-less on Cloud Run"]
        G3["L7 ILB + Routing Broker"]
    end

    subgraph Orchestrator["Root Orchestrator (modules/4-workloads/agents/base-adk-agent)"]
        Root["Client Reasoning Engine<br/>(base-adk-agent)"]
    end

    subgraph MCPServers["MCP Utility Catalog (modules/4-workloads/mcp-servers)"]
        M1["corporate-email"]
        M2["income-verification"]
        M3["legacy-dms"]
    end

    subgraph Downstream["Assistant Agent (modules/4-workloads/agents/a2a-agent)"]
        A2A["a2a-agent Reasoning Engine"]
        DB[(Atomic Cloud SQL Postgres)]
        A2A --> DB
    end

    Gateways ==>|Routes traffic to| Root
    Root ==>|Calls via Gateway MCP URL| MCPServers
    Root ==>|Calls via Gateway A2A URL| A2A
```

---

## 🗺️ Workloads Catalog Index

1. **[Swappable Ingress Gateways](./01-ingress-gateways.md)**
   - Apigee X Enterprise Gateway
   - Lightweight Kong Gateway on Cloud Run
   - Direct Regional L7 Internal HTTP(S) Load Balancer (ILB)
2. **[Standalone API Hub & Composable MCP Server Tools](./02-mcp-tool-servers.md)**
   - Standalone API Hub
   - Corporate Email Server (`mcp-servers/corporate-email/`)
   - Income Verification Server (`mcp-servers/income-verification/`)
   - Legacy DMS Server (`mcp-servers/legacy-dms/`)
3. **[Atomic AI Agents & Database Bootstrapping](./03-ai-agents-and-database.md)**
   - Atomic Mortgage Assistant (`agents/a2a-agent/`)
   - Root Orchestrator Reasoning Engine (`agents/base-adk-agent/`)
   - Database Bootstrap & SQL Lifecycle
   - Live Orchestrator Configurations (Terragrunt Live HCL)
