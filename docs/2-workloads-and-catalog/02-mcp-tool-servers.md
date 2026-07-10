# Standalone API Hub & Composable MCP Server Tools

### B. Standalone API Hub

(`modules/4-workloads/apihub/`)

The API Hub governance catalog runs as an isolated adjacent workload within `prj-gateway`. It automatically catalogs enterprise APIs without interfering with active live traffic routing.

---

---

### C. Composable MCP Server Tools

(`modules/4-workloads/mcp-servers/`)

Shared enterprise backend utilities exposed via the Model Context Protocol (DMS, Email, Income Verification) reside in the `prj-esmeralda-mcps` tool project. Each server is deployed independently to Cloud Run under strict security controls:
*   `no-allow-unauthenticated` status enforced.
*   Outbound traffic mandated to flow 100% via Direct VPC Egress into the Shared VPC network.
*   Explicit custom audiences configured targeting the stable network IP of the Ingress Gateway.
*   Post-deployment triggers executing Python scripts to dynamically catalog tools inside Google Agent Registry.

#### Composable MCP Servers Technical Specifications

# Stage 4 Workloads: Composable MCP Server Tools

This module handles the packaging, containerization, and private execution of corporate backend tools via MCP.

#### 7.4.2 Composable MCP Server Tools (`modules/4-workloads/mcp-servers/`)

To achieve complete modularity and operational flexibility, each Model Context Protocol (MCP) server from the `/tools_mcp/servers/` directory is isolated into a standalone sub-module under `/modules/4-workloads/mcp-servers/`. This allows platform operators to independently update, patch, and redeploy specific tool services without affecting other workloads or gateways.

We define three self-contained sub-modules:
1.  **Corporate Email Tool Server** (`mcp-servers/corporate-email/`)
2.  **Income Verification Tool Server** (`mcp-servers/income-verification/`)
3.  **Legacy DMS Tool Server** (`mcp-servers/legacy-dms/`)

To preserve the zero-trust security paradigm established in Stage 3, each MCP server is deployed to Cloud Run with `no-allow-unauthenticated` status, bound directly to the Shared VPC network via Direct VPC Egress, and protected by Cloud Run IAM invoker bindings. Furthermore, each module incorporates post-deployment registration blocks to dynamically catalog available tools in the GCP Agent Registry and API Hub:

```mermaid
graph TD
    subgraph ClientLayer["Authorized Invokers"]
        Root["sa-base-adk-agent<br/>(Root Orchestrator SA)"]
        TestVM["sa-test-vm<br/>(Jumpbox SA)"]
    end

    subgraph SharedVPC["Shared VPC (prj-net-host)"]
        ILB["Internal Load Balancer / Gateway<br/>(*.internal.gateway)"]
        Egress["Direct VPC Egress Tunnel"]
    end

    subgraph MCPSProject["prj-esmeralda-mcps (Cloud Run Tool Servers)"]
        Email["mcp-servers/corporate-email<br/>(no-allow-unauthenticated)"]
        Income["mcp-servers/income-verification<br/>(no-allow-unauthenticated)"]
        DMS["mcp-servers/legacy-dms<br/>(no-allow-unauthenticated)"]
    end

    subgraph Cataloging["Governance Hub (prj-esmeralda-governance)"]
        Registry["GCP Agent Registry / API Hub"]
    end

    Root & TestVM -->|1. OIDC Token with roles/run.invoker| ILB
    ILB -->|2. Private Routing| Email & Income & DMS
    Email & Income & DMS -. 3. Direct VPC Egress .-> Egress
    Email & Income & DMS -. 4. Post-deploy Script Registration .-> Registry
```

```text
infrastructure/modules/4-workloads/mcp-servers/
├── corporate-email/
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
├── income-verification/
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
└── legacy-dms/
    ├── main.tf
    ├── variables.tf
    └── outputs.tf
```

---

##### A. Sub-Module 1: Corporate Email Server (`mcp-servers/corporate-email/`)

This module deploys the `corporate-email` tool server on Cloud Run. It mounts the service directly inside the Shared VPC to resolve downstream targets privately, locks down the service's HTTP ingress, and grants invoker privileges exclusively to designated agent service accounts.

---

##### B. Sub-Module 2: Income Verification Server (`mcp-servers/income-verification/`)

This module deploys the `income-verification` tool server. It integrates the verification server with regional telemetry logs and enforces strict IAM token-based OIDC protection.

---

##### C. Sub-Module 3: Legacy DMS Server (`mcp-servers/legacy-dms/`)

This module deploys the `legacy-dms` (Document Management System) tool server on Cloud Run. It secures interactions with the core asset store and registers with API Hub.


###### 4. Composed Inputs-Outputs Mapping Matrix

To help operators configure their Terragrunt dependency blocks, this matrix maps the variable bindings across the gateway layer and the composable MCP tool servers:

| MCP Sub-module | Input Source (`terragrunt.hcl`) | Authorized Invokers (`invoker_service_accounts`) | Regional Custom Audience Endpoint | Matches Route Path in ILB |
| :--- | :--- | :--- | :--- | :--- |
| **`corporate-email`** | Output of `prj-esmeralda-mcps` and `base-adk-agent` | `base-adk-agent-sa` email, `test-vm-sa` email | `http://email.internal.gateway/mcp` | `/email/*`, `/email/mcp` |
| **`income-verification`** | Output of `prj-esmeralda-mcps` and `base-adk-agent` | `base-adk-agent-sa` email, `test-vm-sa` email | `http://income-verification.internal.gateway/mcp` | `/income-verification/*` |
| **`legacy-dms`** | Output of `prj-esmeralda-mcps` and `base-adk-agent` | `base-adk-agent-sa` email, `test-vm-sa` email | `http://dms.internal.gateway/mcp` | `/dms/*`, `/dms/mcp` |
