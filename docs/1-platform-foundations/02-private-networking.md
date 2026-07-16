# Stage 2: Private Networking, DNS & PSC

## Architectural Decisions & Design Rationale

Stage 2 provisions a zero-trust Shared VPC network inside `prj-esmeralda-net-host` to connect isolated service projects privately.

### Why Shared VPC Instead of VPC Peering?

*   **Centralized Network Policy Governance**: In enterprise environments, security policies require central network teams to review and audit firewall rules, NAT configurations, and DNS visibility. VPC Peering across seven projects would create a complex mesh of peering connections, bypassing centralized security controls. Shared VPC allows NetOps to control the Host Project (`prj-esmeralda-net-host`), while platform services deployed in other projects consume subnets automatically under the `roles/compute.networkUser` role.
*   **IP Address Conservation**: Standard VPC Peering does not support overlapping subnets and can waste IP space. Centralizing subnets in a Shared VPC ensures efficient Regional IP allocation and simplifies routing tables.

### Why Private Service Connect (PSC) Network Attachments?

*   **Serverless Tenant Network Tunnels**: Vertex AI Reasoning Engines run inside Google-managed tenant networks outside our GCP workspace. By default, these serverless containers query endpoints via the public internet.
*   **Preventing Exfiltration**: To ensure zero data exfiltration, Esmeralda provisions **Private Service Connect (PSC) Network Attachments** in `sb-esmeralda-psc-interface`. When an ADK Reasoning Engine is deployed, Google's serverless runtime establishes a private PSC interface tunnel into the subnetwork, forcing all outbound data traffic (such as database queries and MCP tool calls) to flow privately inside the Shared VPC.

### A. VPC Subnet Topology (`gateway-vpc`)

In the `prj-esmeralda-net-host` project, we allocate the following CIDR ranges:
*   **Core Workload Subnet (`sb-esmeralda-core`)**: `10.0.1.0/24` for internal compute runtimes and test VMs.
*   **Regional Envoy Proxy Subnet (`sb-esmeralda-proxy`)**: `10.9.0.0/24` dedicated exclusively to Envoy-based internal Application Load Balancers (ILB).
*   **PSC NAT Subnet (`sb-esmeralda-psc`)**: `10.10.0.0/24` for outbound Private Service Connect connections.
*   **PSC Interface Subnet (`sb-esmeralda-psc-interface`)**: `10.11.0.0/24` providing local private endpoints for Vertex AI Reasoning Engines operating within Google-managed VPCs.

### B. Private DNS and Gateway Static Routing

To decouple dynamic Vertex AI service URLs, we establish a private Cloud DNS zone named `internal.gateway.` pointing all tool and agent routes to the internal IP (`10.0.0.5`) of the Internal Load Balancer (ILB):
*   `email.internal.gateway` -> `10.0.0.5`
*   `income-verification.internal.gateway` -> `10.0.0.5`
*   `dms.internal.gateway` -> `10.0.0.5`

---

## Detailed Implementation Specifications & HCL Blueprints

This module establishes the Shared VPC network, configures the internal subnet routing topologies, sets up Private Service Connect (PSC) Network Attachments, deploys Google Cloud's Secure Web Proxy (SWP) for audited internet egress, and handles corporate DNS zones. It is designed to run on the Shared VPC Host Project (`prj-esmeralda-net-host`), but can be toggled to a pure-attachment mode for pre-existing (brownfield) customer networks.

#### A. Network Subnet and Routing Architecture
In Greenfield mode, the module provisions a comprehensive hub network with dedicated subnets for core workloads, proxy-only routing, PSC endpoints, serverless integration, and database peering:

```mermaid
graph TD
    subgraph HostProject["prj-esmeralda-net-host (Shared VPC Host)"]
        subgraph VPC["Shared VPC Network (vpc-esmeralda-shared)"]
            SubnetCore["Core Backend Subnet<br/>(sb-esmeralda-core)<br/>10.0.1.0/24"]
            SubnetProxy["Regional Proxy Subnet<br/>(sb-esmeralda-proxy)<br/>10.9.0.0/24 (Active)"]
            SubnetPSC["PSC Subnet<br/>(sb-esmeralda-psc)<br/>10.10.0.0/24"]
            SubnetPSC_I["PSC Interface Subnet<br/>(sb-esmeralda-psc-interface)<br/>10.11.0.0/24"]
            PSA["Private Services Access Range<br/>(sql-peering-range)<br/>10.130.0.0/16"]
        end
        Router["Cloud Router"]
        NAT["Cloud NAT (External Outbound)"]
        SWP["Secure Web Proxy (SWP)<br/>Explicit Gateway Filter<br/>10.0.1.100"]
    end

    Router --> NAT
    SubnetCore --> SWP
    SWP --> Router
    PSA -.->|Internal Peering| DB["Private Cloud SQL<br/>(Database Project)"]
```

##### 1. Subnet Classifications
*   **Core Backend Subnet (`sb-esmeralda-core`)**: Host IP range `10.0.1.0/24`. All primary internal workloads (Cloud Run instances, VPC connectors, and private VMs) operate inside this range. Private Google Access is enabled to permit calling Vertex AI and Secret Manager without traversing the public internet.
*   **Regional Envoy Proxy Subnet (`sb-esmeralda-proxy`)**: Host IP range `10.9.0.0/24`. Required by regional internal Application Load Balancers or secure regional gateways (Apigee or Kong). Configured with purpose `REGIONAL_MANAGED_PROXY` and role `ACTIVE`.
*   **PSC Endpoint Subnet (`sb-esmeralda-psc`)**: Host IP range `10.10.0.0/24` reserved for private Google services PSC endpoints.
*   **PSC Interface Subnet (`sb-esmeralda-psc-interface`)**: Host IP range `10.11.0.0/28`. A highly specific regular subnet utilized solely to bind Google-managed Serverless runtimes.
*   **Private Services Access (`sql-peering-range`)**: Allocated block `10.130.0.0/16` reserved for Serverless Cloud SQL Private Services peering connections.

---

#### B. Serverless Bridges: Private Service Connect (PSC) Network Attachment
Because Vertex AI Reasoning Engines and serverless Cloud Run agents reside natively in Google-managed tenant projects, they do not have direct access to resources inside custom customer VPCs. Esmeralda bridges this boundary using a **PSC Network Attachment**:

```mermaid
sequenceDiagram
    autonumber
    participant Agent as Vertex AI Reasoning Engine / Cloud Run Agent
    participant Attachment as PSC Network Attachment<br/>(sb-esmeralda-psc-interface)
    participant VPC as Shared VPC Network<br/>(sb-esmeralda-core)
    participant DB as Private Cloud SQL

    Agent->>Attachment: 1. Initiates PSC Interface Connection
    Attachment->>VPC: 2. Bridges traffic privately into VPC via PSC-I subnet
    VPC->>DB: 3. Connects to database over private IP (VPC Peering)
```

1.  **Subnet Isolation**: A dedicated regular subnetwork (`sb-esmeralda-psc-interface`) is configured.
2.  **Compute Network Attachment**: The resource `google_compute_network_attachment` references the PSC-I subnet and is set to `ACCEPT_AUTOMATIC`. This creates a PSC interface point. Serverless agents use this resource link to establish incoming tunnels directly into the VPC.
3.  **Firewall Protection**: Ingress firewalls are restricted to only allow ports `22` (SSH for debugging), `443` (HTTPS for APIs), and `ICMP` originating from the PSC Interface subnet (`10.11.0.0/24`).

---

#### C. Egress Auditing & Filtering: Secure Web Proxy (SWP)
To comply with enterprise security requirements, reasoning engines and corporate AI agents are not permitted to establish arbitrary connections to the public internet. Stage 2 provisions a **Secure Web Proxy (SWP)** to audit and control egress traffic:
*   **The SWP Instance**: Deployed via GCP's `google_network_security_gateway_security_policy` (or Fabric's `net-swp` module) in the workloads subnet with a static internal IP (`10.0.1.100`).
*   **Egress Interception**: Workloads communicating via the PSC Interface are configured with an explicit proxy pointing to `10.0.1.100:443`.
*   **Access Rules**: Configured with strict session matching and rules (e.g., matching corporate whitelisted LLMs and SaaS tools while blocking unauthorized domains, with error logging activated).

---

#### D. Shared VPC Attachments & Network User IAM Bindings
To allow the separate workload projects to send private traffic across the Shared VPC, they must be attached as service projects, and their respective service agents must be granted subnet execution rights:

```mermaid
graph LR
    Host["prj-esmeralda-net-host<br/>Shared VPC Host"]
    Subnet["sb-esmeralda-core Subnet"]

    Host -.->|Service Project Attachment| P_MCP["prj-esmeralda-mcps"]
    Host -.->|Service Project Attachment| P_A2A["prj-esmeralda-a2a"]
    Host -.->|Service Project Attachment| P_Root["prj-esmeralda-root-agent"]

    SA_MCP["Cloud Run Service Agent (mcps)"] -->|roles/compute.networkUser| Subnet
    SA_A2A["Cloud Run Service Agent (a2a)"] -->|roles/compute.networkUser| Subnet
    SA_Root["Vertex AI Service Agent (root)"] -->|roles/compute.networkUser| Subnet
```

For workloads in service projects to bind VPC Connectors or establish PSC attachments in the host subnet, the host project must authorize the following service agents with the `roles/compute.networkUser` role on the specific subnet:
1.  **Google APIs Service Agent**: `service-[PROJECT_NUMBER]@cloudservices.gserviceaccount.com` (Handles backend resources orchestration).
2.  **Serverless VPC Access Robot**: `service-[PROJECT_NUMBER]@serverless-robot-prod.iam.gserviceaccount.com` (Enables Serverless VPC Access Connectors).
3.  **Vertex AI Agent**: `service-[PROJECT_NUMBER]@gcp-sa-aiplatform.iam.gserviceaccount.com` (Allows Direct VPC egress for Vertex AI Reasoning Engines).

---

#### E. DNS Service Discovery Layout
To facilitate service discovery, Stage 2 deploys two central private DNS zones visible inside the Shared VPC:
1.  **`esmeralda.internal` Zone**: Used for internal service-to-service discovery (e.g., `dms.esmeralda.internal`, `calculator.esmeralda.internal`).
2.  **`internal.gateway` Zone**: Used to peering PSC interfaces. Contains:
    *   Wildcard record `*.internal.gateway.` resolving to the Internal Load Balancer VIP.
    *   Static record `swp.internal.gateway.` resolving to the Secure Web Proxy IP (`10.0.1.100`).

---

#### F. Greenfield vs. Brownfield (BYO) Logic
When `byo_networking = true` is supplied:
*   We **bypass** creating the VPC, subnetworks (core, proxy, psc, psc-interface), Cloud Router, NAT, and the service networking connection.
*   We **still execute** Shared VPC Host Project enablement, Service Project Attachments, Subnet IAM bindings, and DNS Zones, hooking the three new service projects directly into the customer's pre-configured Shared VPC and SWP infrastructure.

---

### E. Exhaustive Network & Routing Implementation Breakdown

An inspection of `infrastructure/modules/2-networking/` reveals exactly how Esmeralda orchestrates private networking, egress security, and cross-project routing:

```mermaid
flowchart TD
    subgraph Host["Shared VPC Host Project (prj-esmeralda-net-host)"]
        VPC["vpc-esmeralda-shared-{env}"]
        
        subgraph Subnets["5 Specialized Regional Subnets"]
            Core["sb-core (10.0.1.0/24)<br/>Private Google Access"]
            Proxy["sb-proxy (10.9.0.0/24)<br/>REGIONAL_MANAGED_PROXY"]
            PSC["sb-psc (10.10.0.0/24)"]
            PSCI["sb-psc-interface (10.11.0.0/24)"]
            PSA["sql-peering (10.130.0.0/16)"]
        end
        
        SWP["Secure Web Proxy<br/>(10.0.1.100)"]
        Router["Cloud Router & NAT"]
        DNS["Private DNS Zones<br/>esmeralda.internal & internal.gateway"]
    end

    subgraph Attach["5 Service Project Attachments"]
        S1["prj-esmeralda-mcps"]
        S2["prj-esmeralda-a2a"]
        S3["prj-esmeralda-root-agent"]
        S4["prj-esmeralda-gateway (Conditional)"]
        S5["prj-esmeralda-governance (Conditional)"]
    end

    subgraph IAM["Subnet IAM Network Users (roles/compute.networkUser)"]
        Robots["7 Service Robots<br/>(mcps_run, a2a_run, a2a_vertex, root_vertex, gateway_run, plus -re P6SAs)"]
    end

    VPC --- Subnets
    Core --- SWP
    VPC --- Router & DNS
    VPC =====|google_compute_shared_vpc_service_project| Attach
    Robots -->|Granted Network User on Core & PSC-I Subnets| Core & PSCI
```

#### 1. Dynamic Project Lookups & Shared VPC Service Attachments
*   **Data Sources**: Resolves target project numbers dynamically via `data "google_project"` for `mcps`, `a2a`, `root_agent`, and `gateway`, preventing automation deadlocks.
*   **Host Mode Enablement**: Activates Shared VPC host mode (`google_compute_shared_vpc_host_project`) on `var.net_host_project_id`.
*   **Five Service Project Attachments**: Explicitly attaches workload projects (`google_compute_shared_vpc_service_project`) to the host VPC: `mcps`, `a2a`, and `root_agent` unconditionally, and `gateway` and `governance` conditionally (skipped if their BYO flags are true).

#### 2. Greenfield VPC Network & Five Specialized Subnets
When `var.byo_networking = false`, the module provisions a comprehensive VPC (`vpc-esmeralda-shared-{env}`):
1.  **Core Subnet (`sb-esmeralda-core-{env}`)**: CIDR `10.0.1.0/24`, Private Google Access enabled.
2.  **Proxy Subnet (`sb-esmeralda-proxy-{env}`)**: CIDR `10.9.0.0/24`, purpose set to `REGIONAL_MANAGED_PROXY` with role `ACTIVE`.
3.  **PSC Subnet (`sb-esmeralda-psc-{env}`)**: CIDR `10.10.0.0/24` for internal PSC endpoints.
4.  **PSC Interface Subnet (`sb-esmeralda-psc-interface-{env}`)**: CIDR `10.11.0.0/24` for serverless VPC interface tunnels.
5.  **Private Services Access Peering (`sql-peering-range-{env}`)**: Internal global address `/16` block `10.130.0.0/16` paired with `google_service_networking_connection.sql_connection` for managed Cloud SQL private connectivity.

#### 3. Cloud Router, Cloud NAT & Egress Firewalls
*   **Cloud Router & NAT**: Provisions `cr-esmeralda-nat-{env}` and `nat-esmeralda-outbound-{env}` configured with `AUTO_ONLY` IP allocation across `ALL_SUBNETWORKS_ALL_IP_RANGES`.
*   **PSC Interface Firewall (`allow-psc-interface-ingress-{env}`)**: Restricts ingress from `10.11.0.0/24` exclusively to TCP ports `22`, `443`, and `ICMP` protocol packets.
*   **IAP SSH Firewall (`allow-iap-ssh-{env}`)**: Permits Identity-Aware Proxy ingress from Google's standard IAP range (`35.235.240.0/20`) on port `22`.

#### 4. Serverless PSC Attachment & Secure Web Proxy (SWP)
*   **PSC Network Attachment (`gateway-psc-interface-attachment-{env}`)**: Configured with `ACCEPT_AUTOMATIC` preference bound to the PSC-I subnet, allowing serverless reasoning engines to establish private VPC tunnels.
*   **Secure Web Proxy (`gateway-swp-{env}`)**: Deployed via Google's `net-swp` Fabric module at static IP `10.0.1.100` on the core subnet, enforcing explicit HTTP/HTTPS session matching rules (`allow-all`).

#### 5. Subnet IAM Network User Bindings & Reasoning Engine Extensions
To allow service robots to launch containers and connectors inside host subnets, Esmeralda aggregates 7 service identities in `local.subnet_network_users`:
*   `mcps_run`, `a2a_run`, `a2a_vertex`, `root_vertex`, and `gateway_run`.
*   **Reasoning Engine Identity Extension**: Automatically derives the specialized Vertex AI Reasoning Engine service account by performing string replacement on the Vertex agent email: `@gcp-sa-aiplatform.iam.gserviceaccount.com` $\rightarrow$ `@gcp-sa-aiplatform-re.iam.gserviceaccount.com`.
*   **Role Assignments**: After a 30-second IAM propagation sleep (`time_sleep.iam_propagation`), binds `roles/compute.networkUser` for all 7 identities onto both the Core Workload subnet and the PSC Interface subnet.

#### 6. Private DNS Managed Zones
*   **`esmeralda.internal.` Zone**: Managed private DNS zone (`esmeralda-private-dns-{env}`) with private visibility locked to the Shared VPC network.
*   **`internal.gateway.` Zone**: Fabric `dns` module deploying private DNS records for internal infrastructure: `A *` mapped to LB VIP placeholder `10.0.1.200`, and `A swp` mapped to `10.0.1.100`.

#### 7. Module Outputs (`outputs.tf`)
Exports 7 networking primitives: `network_id`, `subnet_id`, `subnet_name`, `dns_zone_name`, `dns_zone_dns_name`, `psc_network_attachment_id`, and `secure_web_proxy_ip`.

---

#### F. File Inventory & Blueprints

```text
infrastructure/modules/2-networking/
├── versions.tf          # Mandates HashiCorp Google provider bounds (>= 5.0)
├── variables.tf         # Host project IDs, service project IDs, CIDR overrides, and BYO flags
├── main.tf              # Greenfield VPC, NAT, PSA + PSC Attachments, SWP, IAM network users, Cloud DNS
└── outputs.tf           # Exports VPC network ID, Subnet self-links, and DNS private zone name
```
