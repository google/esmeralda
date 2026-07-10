# Standalone API Hub & Composable MCP Server Tools

### B. Standalone API Hub

(`modules/4-workloads/apihub/`)

The API Hub governance catalog runs as an isolated adjacent workload within `prj-gateway`. It automatically catalogs enterprise APIs without interfering with active live traffic routing.

> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform implementation for standalone API Hub creation and activation is available at:
> 👉 [`main.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/apihub/main.tf)


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

To preserve the zero-trust security paradigm established in Stage 3, each MCP server is deployed to Cloud Run with `no-allow-unauthenticated` status, bound directly to the Shared VPC network via Direct VPC Egress, and protected by Cloud Run IAM invoker bindings. Furthermore, each module incorporates post-deployment registration blocks to dynamically catalog available tools in the GCP Agent Registry and API Hub.

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

###### 1. Variables Specification (`variables.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform variables configuration for the Corporate Email MCP tool server is available at:
> 👉 [`variables.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/mcp-servers/corporate-email/variables.tf)


###### 2. Implementation Blueprint (`main.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Cloud Run deployment configuration and Shared VPC binding for Corporate Email is available at:
> 👉 [`main.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/mcp-servers/corporate-email/main.tf)


###### 3. Outputs Specification (`outputs.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The exported service URLs from the Corporate Email MCP module are available at:
> 👉 [`outputs.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/mcp-servers/corporate-email/outputs.tf)


---

##### B. Sub-Module 2: Income Verification Server (`mcp-servers/income-verification/`)

This module deploys the `income-verification` tool server. It integrates the verification server with regional telemetry logs and enforces strict IAM token-based OIDC protection.

###### 1. Variables Specification (`variables.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform variables configuration for the Income Verification MCP tool server is available at:
> 👉 [`variables.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/mcp-servers/income-verification/variables.tf)


###### 2. Implementation Blueprint (`main.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The secure deployment blueprint for the Income Verification tool server is available at:
> 👉 [`main.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/mcp-servers/income-verification/main.tf)


###### 3. Outputs Specification (`outputs.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The exported endpoint URL for Income Verification is available at:
> 👉 [`outputs.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/mcp-servers/income-verification/outputs.tf)


---

##### C. Sub-Module 3: Legacy DMS Server (`mcp-servers/legacy-dms/`)

This module deploys the `legacy-dms` (Document Management System) tool server on Cloud Run. It secures interactions with the core asset store and registers with API Hub.

###### 1. Variables Specification (`variables.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The Terraform variables configuration for Legacy DMS is available at:
> 👉 [`variables.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/mcp-servers/legacy-dms/variables.tf)


###### 2. Implementation Blueprint (`main.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The private Cloud Run deployment configuration for Legacy DMS is available at:
> 👉 [`main.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/mcp-servers/legacy-dms/main.tf)


###### 3. Outputs Specification (`outputs.tf`)
> [!TIP]
> 📁 **Source Code File Available:**
> The exported URL generated by the Legacy DMS module is available at:
> 👉 [`outputs.tf`](../migration/02_workloads_and_delivery/infrastructure/modules/4-workloads/mcp-servers/legacy-dms/outputs.tf)


###### 4. Composed Inputs-Outputs Mapping Matrix

To help operators configure their Terragrunt dependency blocks, this matrix maps the variable bindings across the gateway layer and the composable MCP tool servers:

| MCP Sub-module | Input Source (`terragrunt.hcl`) | Authorized Invokers (`invoker_service_accounts`) | Regional Custom Audience Endpoint | Matches Route Path in ILB |
| :--- | :--- | :--- | :--- | :--- |
| **`corporate-email`** | Output of `prj-esmeralda-mcps` and `base-adk-agent` | `base-adk-agent-sa` email, `test-vm-sa` email | `http://email.internal.gateway/mcp` | `/email/*`, `/email/mcp` |
| **`income-verification`** | Output of `prj-esmeralda-mcps` and `base-adk-agent` | `base-adk-agent-sa` email, `test-vm-sa` email | `http://income-verification.internal.gateway/mcp` | `/income-verification/*` |
| **`legacy-dms`** | Output of `prj-esmeralda-mcps` and `base-adk-agent` | `base-adk-agent-sa` email, `test-vm-sa` email | `http://dms.internal.gateway/mcp` | `/dms/*`, `/dms/mcp` |
