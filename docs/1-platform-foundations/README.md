# Platform Foundations (Stages 1, 2, and 3)

This section of the documentation unifies all conceptual details, architectural infrastructure decisions, FinOps principles, and production-ready Terraform/HCL blueprints for Esmeralda's foundational platform (Stages 1, 2, and 3).

### The 3-Stage Landing Zone Foundation Pipeline

Before any AI reasoning engine or MCP tool container can be executed, Esmeralda establishes a zero-trust enterprise landing zone through three sequential infrastructure stages:

```mermaid
flowchart TD
    subgraph Stage1["Stage 1: Projects & FinOps (modules/1-projects)"]
        P1["Provision up to 7 Isolated GCP Projects<br/>(net_host, gateway, cicd, mcps, a2a, root_agent, governance)"]
        P2["Link Corporate Billing & Enable Service APIs"]
        P1 --> P2
    end

    subgraph Stage2["Stage 2: Private Networking (modules/2-networking)"]
        N1["Deploy Shared VPC Network in prj-net-host"]
        N2["Provision Subnets: core, proxy, psc, psc-interface"]
        N3["Configure Cloud NAT, SWP & Private DNS Zones"]
        N1 --> N2 --> N3
    end

    subgraph Stage3["Stage 3: Security & Telemetry (modules/3-security)"]
        S1["Centralize KMS Keyrings & CMEK Keys in prj-esmeralda-governance"]
        S2["Provision Secret Manager Secrets & BigQuery Audit Sinks"]
        S3["Create Workload SAs with Strict Least-Privilege IAM Roles"]
        S1 --> S2 --> S3
    end

    Stage1 ==>|Provides Project IDs & Service Agents| Stage2
    Stage2 ==>|Provides VPC, Subnet & DNS Self-Links| Stage3
    Stage3 ==>|Ready for AI Application Catalog Workloads| Catalog["Stage 4: Composable Workloads Catalog"]
```

---

## Foundations Deployment Index

1. **[Stage 1: Foundational Projects, Billing (FinOps), and APIs](./01-projects-and-finops.md)**
   - Architectural & FinOps Overview
   - Technical Specifications & HCL Blueprints (`modules/1-projects/`)
2. **[Stage 2: Private Networking, DNS, and Private Service Connect (PSC)](./02-private-networking.md)**
   - Network Topology & Secure Egress Overview
   - Technical Specifications & HCL Blueprints (`modules/2-networking/`)
3. **[Stage 3: Security, CMEK Keys, Secrets, and Identities](./03-security-iam-and-telemetry.md)**
   - Encryption, Service Accounts, and Least-Privilege IAM Overview
   - Technical Specifications & HCL Blueprints (`modules/3-security/`)
