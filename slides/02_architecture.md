# Slide 2: The Reference Agentic Architecture

## 4-Layer Enterprise Modular Architecture Stack

```mermaid
flowchart TB
    subgraph Layer1["Layer 1: Projects, FinOps & Landing Zone (Stage 1)"]
        L1_Desc["7 Isolated GCP Projects • API Enablement • Billing Bindings • Cost Labels"]
    end

    subgraph Layer2["Layer 2: Zero-Trust Networking & Egress (Stage 2)"]
        L2_Desc["Shared VPC • Core/PSC Subnets • Private DNS • Cloud NAT • Secure Web Proxy (SWP)"]
    end

    subgraph Layer3["Layer 3: Security, CMEK Keys & Audit Sinks (Stage 3)"]
        L3_Desc["KMS Encryption Keyrings • Secret Manager • Workload Service Accounts • BigQuery Log Sinks"]
    end

    subgraph Layer4["Layer 4: Composable AI Workloads Catalog (Stage 4)"]
        L4_Desc["Swappable Gateways (Apigee/Kong/ILB) • MCP Tool Servers • ADK Reasoning Engines"]
    end

    Layer1 ==> Layer2 ==> Layer3 ==> Layer4

    style Layer1 stroke:#4285F4,stroke-width:2px
    style Layer2 stroke:#34A853,stroke-width:2px
    style Layer3 stroke:#EA4335,stroke-width:2px
    style Layer4 stroke:#FBBC05,stroke-width:2px
```

---

## Multi-Project Topology & Governance (7 Isolated GCP Projects)

```mermaid
flowchart TB
    subgraph GovernanceHub["Central Governance & Deployment"]
        P_CICD["prj-esmeralda-cicd-artifacts<br/>(Container Registries & CI/CD)"]
        P_Gov["prj-esmeralda-governance<br/>(KMS, Secrets & Audit Log Sinks)"]
    end

    subgraph SharedVPC["Shared VPC Network Scope (prj-esmeralda-net-host)"]
        P_Gat["prj-esmeralda-gateway<br/>(API Gateway Ingress)"]
        P_Root["prj-esmeralda-root-agent<br/>(Business Orchestrator)"]
        P_MCP["prj-esmeralda-mcps<br/>(Central Tool Servers)"]
        P_A2A["prj-esmeralda-a2a<br/>(AI Platform & Postgres)"]

        P_Gat --> P_Root
        P_Root ==> P_MCP
        P_Root ==> P_A2A
    end

    P_CICD -. Container Images .-> P_Root
    P_CICD -. Container Images .-> P_MCP
    SharedVPC -- Audit Logs & Spans --> P_Gov

    style SharedVPC stroke:#4285F4,stroke-width:2px
    style P_Gov stroke:#EA4335,stroke-width:2px
    style P_CICD stroke:#FBBC05,stroke-width:2px
    style P_Gat stroke:#4285F4,stroke-width:2px
    style P_Root stroke:#34A853,stroke-width:2px
```

---

## Runtime Orchestration: Multi-Agent Engine & MCP Ecosystem

```mermaid
flowchart TB
    Client(["Client / Front-End Application"]) ==> Gateway["Ingress Gateway<br/>(Apigee / Kong / ILB)"]

    subgraph OrchestrationProject["prj-esmeralda-root-agent"]
        Root["Root Orchestrator Agent<br/>(Vertex AI Reasoning Engine / ADK)<br/>Powered by Gemini 2.5 Flash"]
    end

    Gateway ==>|Authenticated Internal Route| Root

    subgraph ToolsProject["prj-esmeralda-mcps"]
        T1["Corporate Email MCP<br/>(Cloud Run)"]
        T2["Income Verification MCP<br/>(Cloud Run)"]
        T3["Legacy DMS MCP<br/>(Cloud Run)"]
    end

    subgraph A2AProject["prj-esmeralda-a2a"]
        A2A["Specialized Assistant Agent<br/>(A2A Reasoning Engine)"]
        DB[(Cloud SQL Postgres Task Store)]
        A2A <--> DB
    end

    Root ==>|Model Context Protocol JSON| T1
    Root ==>|Model Context Protocol JSON| T2
    Root ==>|Model Context Protocol JSON| T3
    Root ==>|Agent-to-Agent Protocol| A2A

    style Root stroke:#34A853,stroke-width:2px
    style Gateway stroke:#4285F4,stroke-width:2px
    style DB stroke:#FBBC05,stroke-width:2px
```
