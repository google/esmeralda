# 📖 Esmeralda Architecture & Documentation Hub

Welcome to the **Official Esmeralda Documentation Hub**.

If you are new to the project, this guide will give you a **crystal-clear understanding** of what Esmeralda is, why it was designed this way, and how the entire ecosystem fits together.

---

## 💡 The 60-Second Mental Model: What is Esmeralda?

> *"O que está embaixo é como o que está no alto, e o que está no alto é como o que está embaixo."*  
> — **A Tábua de Esmeralda** (Hermes Trismegisto/Jorge Ben Jor)

In most enterprises, AI Agent prototypes remain stuck in notebooks or local scripts because **taking agents to production is an infrastructure, security, and governance challenge**, not just a prompt engineering challenge.

**Esmeralda bridges this gap by enforcing a strict duality:**

```
               ┌────────────────────────────────────────────────────────┐
               │  🟢 NO ALTO (Application Layer - apps/)                │
               │  Pure Python reasoning, Gemini 3.7 Flash, ADK agents,  │
               │  and standardized Model Context Protocol (MCP) tools.   │
               └─────────────────────────┬──────────────────────────────┘
                                         │  Zero-Trust Integration
               ┌─────────────────────────┴──────────────────────────────┐
               │  🔵 EMBAIXO (Platform Layer - infrastructure/)         │
               │  5-layer Terragrunt IaC, Shared VPC, mTLS SPIFFE,      │
               │  Central Agent Gateway, Model Armor & BigQuery FinOps. │
               └────────────────────────────────────────────────────────┘
```

---

## 🎭 The Two Personas: Separation of Concerns

Esmeralda is designed around two distinct developer personas who collaborate without stepping on each other's toes:

| Persona | Where they work | What they care about | What they NEVER have to touch |
| :--- | :--- | :--- | :--- |
| 🧑‍💻 **AI / Application Developer** | [`apps/`](../apps/) | Writing agent logic with **Google ADK**, defining **MCP tool servers** (FastAPI/FastMCP), testing prompts with **Gemini 3.7 Flash**, and orchestrating multi-agent **A2A protocols**. | Terraform, Terragrunt, VPC peering, KMS IAM roles, subnets, firewall rules, or DNS zones. |
| 👷 **Platform / SecOps Engineer** | [`infrastructure/`](../infrastructure/) | Managing declarative **Terragrunt & Terraform modules**, isolated GCP projects, Private Service Connect (PSC), **Central Agent Gateway** egress, and **BigQuery FinOps** chargeback views. | Python application business logic, agent prompts, or internal tool schemas. |

---

## 🔄 End-to-End Request & Security Lifecycle

Here is what happens under the hood when a user submits a prompt (e.g., *"Process mortgage application 2024-7891 for Julian Sterling"*):

```mermaid
sequenceDiagram
    autonumber
    actor User as Client
    participant Root as Root Coordinator<br/>(Vertex Agent)
    participant A2A as Specialist Agent<br/>(Vertex A2A)
    participant MCP as MCP Tools<br/>(Cloud Run)
    participant AGW as Agent Gateway<br/>(Central Proxy)
    participant MA as Model Armor<br/>(Guardrails)
    participant Gemini as Gemini 3.7 Flash<br/>(Vertex AI API)
    participant FinOps as Central Governance<br/>(BigQuery FinOps)

    User->>Root: 1. Ingress User Request
    Root->>A2A: 2. Delegate Task (A2A Protocol / PSC)
    
    Note over A2A,MCP: Phase A — Private Intranet Tool Execution
    A2A->>MCP: 3. Query Documents (Legacy DMS)
    A2A->>MCP: 4. Verify Payroll (Income Verification)
    A2A->>MCP: 5. Fetch Applicant History (Corporate Email)

    Note over A2A,Gemini: Phase B — Zero-Trust Model Egress via Central Gateway
    A2A->>AGW: 6. Model Egress with mTLS Workload Identity (SPIFFE)
    AGW->>MA: 7. Sanitize Content (PII & Injection Filters)
    AGW->>FinOps: 8. Emit Real-time Token & Chargeback Telemetry
    AGW->>Gemini: 9. Forward Authorized Request
    Gemini-->>AGW: 10. Model Response & Reasoning Thoughts
    AGW-->>A2A: 11. Validated Model Output

    A2A-->>Root: 12. Structured Assessment
    Root-->>User: 13. Final User Response
```

---

## 🏗️ The Layered Infrastructure Blueprint

Infrastructure is built from the ground up in **6 decoupled layers** using Terragrunt. Layer 0 is shared by all environments; layers 1 to 5 are deployed once per environment (`dev`, `prd`):

```mermaid
flowchart LR
    L0["Layer 0 (shared)<br/><b>CI/CD</b><br/>Artifact Registry & Cloud Build"] --> L1["Layer 1<br/><b>Projects & APIs</b><br/>Project factory & APIs"]
    L1 --> L2["Layer 2<br/><b>Networking</b><br/>Shared VPC & PSC"]
    L2 --> L3["Layer 3<br/><b>Security & IAM</b><br/>KMS CMEK, Secrets & internal CA"]
    L3 --> L4["Layer 4<br/><b>Governance Hub</b><br/>Agent Gateway, Model Armor & FinOps"]
    L4 --> L5["Layer 5<br/><b>Workloads</b><br/>MCP servers, agents & Kong"]
```

0. 🏭 **Layer 0: Shared CI/CD (`live/shared/layer-0-cicd`)**:
   One CI/CD project for all environments, with a mutable dev image repository and an immutable release repository. Images are built once and promoted by digest from dev to release.
1. 🏢 **[Layer 1: Projects & FinOps (`layer-1-projects`)](./1-platform-foundations/01-projects-and-finops.md)**:
   Provisions the isolated GCP projects (`net-host`, `gateway`, `governance`, `mcps`, `ai-coe-agents`, `cx-agents`) and activates required APIs. Agent projects are named after the team that owns them.
2. 🌐 **[Layer 2: Private Networking (`layer-2-networking`)](./1-platform-foundations/02-private-networking.md)**:
   Deploys the central Shared VPC, private subnets, Cloud DNS zones (`*.esmeralda.internal`), and Private Service Connect (PSC) attachments.
3. 🔐 **[Layer 3: Security & Secrets (`layer-3-security`)](./1-platform-foundations/03-security-iam-and-telemetry.md)**:
   Configures Cloud KMS CMEK encryption keyrings, Secret Manager secrets, the internal root CA, and workload Service Accounts with least-privilege IAM bindings.
4. 🛡️ **[Layer 4: Central Governance Hub (`layer-4-governance`)](./3-agentops-and-lifecycle/README.md)**:
   Establishes the enterprise control plane before any workload runs:
   * **Central Agent Gateway**: Intercepts agent egress using `AGENT_TO_ANYWHERE` with mTLS SPIFFE identity.
   * **Model Armor**: Enforces PII sanitization and prompt injection filters.
   * **FinOps Analytics**: Sinks telemetry events to BigQuery views (`vw_monthly_agent_chargeback`, `vw_request_level_telemetry`) and Cloud Monitoring dashboards.
5. ⚙️ **[Layer 5: Workloads & Tool Catalog (`layer-5-workloads`)](./2-workloads-and-catalog/README.md)**:
   Deploys the runtime applications, bound to the layer-4 gateway and registry:
   * **MCP Microservices** (Cloud Run): Corporate Email, Income Verification, Legacy DMS.
   * **AI Reasoning Engines** (Vertex AI): the CX team's orchestrator (`cx-mortgage-orchestrator`) consuming the AI CoE's reusable A2A specialist (`ai-coe-mortgage-specialist`, backed by Cloud SQL).

---

## 🧭 Documentation Roadmap & Sub-Guides

Deep-dive into specific areas of the platform:

| Guide | Description | Key Topics |
| :--- | :--- | :--- |
| 🏢 **[1. Platform Foundations](./1-platform-foundations/README.md)** | Core cloud landing zone and infrastructure specs. | [Projects & APIs](./1-platform-foundations/01-projects-and-finops.md), [Shared VPC Networking](./1-platform-foundations/02-private-networking.md), [Security, CMEK & IAM](./1-platform-foundations/03-security-iam-and-telemetry.md). |
| 🤖 **[2. Workloads & Catalog](./2-workloads-and-catalog/README.md)** | Runtimes, microservices, and AI engines. | [Ingress Gateways](./2-workloads-and-catalog/01-ingress-gateways.md), [MCP Tool Servers](./2-workloads-and-catalog/02-mcp-tool-servers.md), [Reasoning Engines & Database](./2-workloads-and-catalog/03-ai-agents-and-database.md). |
| 📊 **[3. AgentOps & Governance](./3-agentops-and-lifecycle/README.md)** | Enterprise governance, security, and observability. | [Central Agent Gateway](./3-agentops-and-lifecycle/01-central-agent-gateway.md), [Centralized Monitoring & FinOps Dashboards](./3-agentops-and-lifecycle/03-centralized-monitoring-and-dashboards.md), Multi-repo SDLC, FinOps chargebacks. |
| 🤝 **[Contributing Guidelines](./contributing.md)** | Contribution standards and testing guidelines. | Git conventions, PR requirements, test coverage expectations. |
| 📜 **[Code of Conduct](./code-of-conduct.md)** | Community engagement standards. | Respect, inclusivity, and community ethics. |
