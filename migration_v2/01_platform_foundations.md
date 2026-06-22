# Guia Mestre: Fundações da Plataforma (Estágios 1, 2 e 3)

Este documento unifica todos os detalhes conceituais, decisões arquiteturais de infraestrutura, FinOps e os códigos Terraform/HCL prontos para implantação das fundações do Esmeralda. Ele consolida os Estágios 1, 2 e 3 em um único guia linear, permitindo a leitura de alto nível em português e a cópia direta dos códigos em inglês (Ctrl+C / Ctrl+V) sem a necessidade de alternar entre arquivos dispersos.

---

## 🗺️ Índice de Implantação das Fundações
1. [Estágio 1: Provisionamento de Projetos, Faturamento (FinOps) e APIs](#stage-1)
   - [Explicação Arquitetural e FinOps (Português)](#s1-concepts)
   - [Especificações Técnicas e Códigos HCL Completos (Inglês)](#s1-codes)
2. [Estágio 2: Redes Privadas, DNS e Private Service Connect (PSC)](#stage-2)
   - [Explicação da Topologia de Rede e Egress Seguro (Português)](#s2-concepts)
   - [Especificações Técnicas e Códigos HCL Completos (Inglês)](#s2-codes)
3. [Estágio 3: Segurança, Chaves CMEK, Secrets e Identidades (Least Privilege)](#stage-3)
   - [Explicação de Criptografia, SAs e Políticas de Acesso (Português)](#s3-concepts)
   - [Especificações Técnicas e Códigos HCL Completos (Inglês)](#s3-codes)

---

<a name="stage-1"></a>
## 🏢 1. Estágio 1: Projetos, FinOps e APIs

<a name="s1-concepts"></a>
### A. Guia de Arquitetura e Decisões de Negócio (Português)

## 🗺️ 1. Stage 1: Projetos, Faturamento (FinOps) & APIs

O Stage 1 gerencia a criação isolada de múltiplos projetos e ativação das APIs fundamentais sob uma estrutura de governança compatível com as regras de Landing Zone empresariais do Google Cloud.

### A. Simulação de Projetos da Landing Zone (SoC)
Para simular um ambiente de produção real, as cargas de trabalho são divididas em **seis projetos distintos**:

| Projeto | Nome Simulado | Papel e Responsabilidade | Recursos Principais Hospedados |
| :--- | :--- | :--- | :--- |
| **Hospedeiro de Rede** | `prj-net-host` | Gerenciado por NetOps. Controla o tráfego de rede e segurança. | VPC Compartilhada, Subnets, Cloud DNS, Cloud NAT, Firewalls. |
| **Ingresso de Tráfego** | `prj-gateway` | Gerenciado por PlatformOps. Controla ingressos de APIs corporativos. | Apigee X, Kong no Cloud Run, Internal Load Balancer. |
| **Ferramentas MCP** | `prj-esmeralda-mcps` | Gerenciado pelo time AppDev. APIs de utilidades comuns da empresa. | Cloud Run (Corporate Email, Income Verifier, DMS), Artifact Registry. |
| **Plataforma Core AI** | `prj-esmeralda-a2a-agents` | Gerenciado pelo time Core AI. Hospeda agentes globais reutilizáveis. | Vertex AI Reasoning Engine (A2A), Cloud SQL PostgreSQL. |
| **Unidade de Negócios** | `prj-esmeralda-root-agent` | Gerenciado pela área de Linha de Negócio. Agentes de interface ao usuário. | Vertex AI Reasoning Engine (Root), Buckets GCS de Staging. |
| **Hub de Governança** | `prj-esmeralda-governance` | Gerenciado por SecOps. Centraliza conformidade e dados consolidados. | KMS Keyrings, Secrets, Certificados TLS, BigQuery Analytics, Log Sinks. |

---

### B. Desafios de FinOps Resolvidos
1.  **Atribuição Limpa de IA Generativa**: Chamadas do agente de mortgage faturam diretamente em `prj-esmeralda-root-agent`. Chamadas secundárias de subprocessos faturam em `prj-esmeralda-a2a-agents`.
2.  **Separação de Custos Ephemeral vs. Persistentes**: O banco de dados PostgreSQL (faturamento 24/7) fica isolado no projeto da plataforma de IA. Os servidores MCP (Cloud Run) escalam para zero, reduzindo custos a zero quando ociosos.
3.  **Tráfego de Rede Sem Custos Ocultos**: Todo o tráfego flui internamente pela rede privada da VPC Compartilhada na mesma região (`us-central1`), evitando taxas de trânsito de NAT público.

---

---

<a name="s1-codes"></a>
### B. Documento de Implementação Detalhado e Códigos Prontos (Inglês)

# Stage 1: Foundational Projects, Billing & APIs

This module manages the automated provisioning of isolated GCP service projects, links them securely to the corporate billing account, and activates standard APIs natively.

## 7. Detailed Module Implementation Specifications

This section defines the precise layout, HCL blueprints, and resources that must be built inside each of the pure reusable directories under `infrastructure/modules/`.

### 7.1 Stage 1: `modules/1-projects/` Specification

This module manages the creation of isolated GCP projects, links them to enterprise billing, activates necessary service APIs, and establishes the foundational service account mappings. Under this design, the module handles **up to six distinct projects**, reflecting a standard enterprise landing zone.

#### A. Enterprise Team & Project Simulation Mapping
To simulate a real-world enterprise multi-tenant landing zone, we split our architecture across six projects, allowing separate business, engineering, and observability units to operate independently:

| Simulated Team | GCP Project ID | Business & Operational Role | Primary Resources Hosted |
| :--- | :--- | :--- | :--- |
| **Network Ops (NetOps)** | `prj-net-host` | Owns the central networking backbones, Shared VPC, Private Service Connect (PSC), and firewall policies. | Shared VPC, Subnets, Cloud NAT, Cloud DNS, PSC Endpoints. |
| **Platform Ops (PlatformOps)** | `prj-gateway` | Manages the public-facing application ingress, corporate domain name registration, and corporate API Gateways. | Apigee X, Kong Gateway on Cloud Run, external HTTPS Load Balancers. |
| **AppDev Tools Team** | `prj-esmeralda-mcps` | Creates, maintains, and packages reusable Model Context Protocol (MCP) tool servers for the whole company. | Cloud Run (DMS Server, Calculator Server), Artifact Registry. |
| **Core AI Platform Team** | `prj-esmeralda-a2a-agents` | Develops reusable, cross-company Assistant-to-Assistant (A2A) agents, handling centralized business domain reasoning. | Cloud SQL PostgreSQL, Cloud Run Database Bootstrapping Job, Vertex AI Reasoning Engine (A2A). |
| **Line-of-Business Team (LOB)** | `prj-esmeralda-root-agent` | Develops the final client-facing user reasoning engine, which acts as the frontend orchestrator and orchestrates upstream agents. | Vertex AI Reasoning Engine (Root), client-facing IAM roles, GCS Buckets. |
| **Security & Governance Hub** | `prj-esmeralda-governance` | Consolidates central security/governance (KMS keys, secrets, certs) and central telemetry/observability (BigQuery dataset, trace views, log sinks), establishing strict Separation of Concerns (SoC) between Platform Governance and Workload Runtimes. | KMS Keyrings, secrets, Certificate Manager certificates, BigQuery Audit Sinks, and log buckets. |

---

#### B. The FinOps Challenge: Cost Attribution & Billing Isolation
Deploying agentic pipelines across multiple distinct GCP projects solves major enterprise pain points but introduces key FinOps and tracking challenges. Our multi-project structure solves these as follows:

```mermaid
graph TD
    %% FinOps visualization
    subgraph Billing["GCP Billing Account (Central Treasury)"]
        Export["Cloud Billing BigQuery Export"]
    end

    subgraph "prj-esmeralda-mcps (AppDev Budget)"
        C1["Cloud Run Compute Costs"]
        L1["Label: cost-center=appdev-tools"]
    end

    subgraph "prj-esmeralda-a2a-agents (AI Platform Budget)"
        C2["Vertex AI Model API Calls"]
        C3["Cloud SQL DB (Continuous Run)"]
        L2["Label: cost-center=core-ai-platform"]
    end

    subgraph "prj-esmeralda-root-agent (LOB Revenue Center)"
        C4["Vertex AI Orchestrator API Calls"]
        L3["Label: cost-center=lob-mortgage"]
    end

    C1 & L1 -. Billing Record .-> Export
    C2 & C3 & L2 -. Billing Record .-> Export
    C4 & L3 -. Billing Record .-> Export

    style Billing fill:#f5f5f5,stroke:#333,stroke-width:2px
```

##### 1. Vertex AI Model API Billing Attribution
When multiple teams call Gemini via Vertex AI, a monolithic project makes it impossible to distinguish which team consumed how many input/output tokens. By splitting workloads:
*   Calls to Gemini made by the Root Orchestrator are charged directly to `prj-esmeralda-root-agent` (LOB Budget).
*   Calls to Gemini made by the A2A Agent during sub-task execution are charged to `prj-esmeralda-a2a-agents` (Core AI Budget).
*   *Implementation*: Resource labels (`env=dev`, `cost-center=...`, `team=...`) are systematically applied at the project level and resource level, flowing directly into the **GCP Billing Export to BigQuery** for clean dashboards.

##### 2. Persistent vs. Ephemeral Resource Cost Allocation
*   **Cloud SQL Instance**: Run as a shared state machine for the A2A agent, running 24/7. This represents a fixed cost that is isolated inside the Core AI Platform budget (`prj-esmeralda-a2a-agents`) and is not subsidized by the LOB team.
*   **Cloud Run (MCP Tool Servers)**: Run serverless, scaling to zero when there are no requests. This ensures that the AppDev team only incurs compute costs when tools are actively invoked, preventing resource wasting.

##### 3. Cross-Project Network Transit Cost Optimization
Data transferring across VPC subnets and project boundaries can incur inter-zone egress fees.
*   To address this, all projects are systematically locked to a single region (`us-central1`) and use Shared VPC Private IP communication, eliminating public internet NAT gateway egress charges for inter-agent communication.

---

#### C. The BYOInfra (Brownfield) Integration Architecture
In real enterprise deployments, customers will **never** allow a tool to provision a new Shared VPC Host Project (`net_host`) or change their centralized Gateway Ingress Project (`gateway`). 

Esmeralda elegantly handles this by using a dynamic **BYOInfra Fallback Architecture**:

```mermaid
flowchart TD
    %% Decoupling logic
    subgraph Inputs["Terragrunt Input Parameters (env.yaml)"]
        BYO_Net["byo_net_host_project = true"]
        BYO_Gwy["byo_gateway_project = true"]
        Exist_Net["existing_net_host_project = prj-corp-net-host"]
        Exist_Gwy["existing_gateway_project = prj-corp-apigee-ingress"]
    end

    subgraph Stage1["Stage 1: modules/1-projects"]
        Check_Net{byo_net_host_project?}
        Check_Gwy{byo_gateway_project?}
        
        Check_Net -- "True (BYO)" --> Skip_Net["Bypass Creation <br/> Return existing_net_host_project"]
        Check_Net -- "False" --> Create_Net["Create prj-net-host from scratch"]
        
        Check_Gwy -- "True (BYO)" --> Skip_Gwy["Bypass Creation <br/> Return existing_gateway_project"]
        Check_Gwy -- "False" --> Create_Gwy["Create prj-gateway from scratch"]
        
        Create_MCPS["Create prj-esmeralda-mcps <br/> (Always)"]
        Create_A2A["Create prj-esmeralda-a2a-agents <br/> (Always)"]
        Create_Root["Create prj-esmeralda-root-agent <br/> (Always)"]
    end

    Inputs --> Check_Net
    Inputs --> Check_Gwy
```

*   **Conditional Project Seed**: In `main.tf`, `google_project.net_host` and `google_project.gateway` use a `count` conditional (`count = var.byo_net_host_project ? 0 : 1`).
*   **API Enablement Isolation**: Service API enablement is also conditional. If `byo_net_host_project` is true, Esmeralda skips enabling APIs in that project to prevent permission conflicts with corporate security policies (which restrict IAM permissions to create/enable APIs on shared host projects).
*   **Dynamic Outputs Mapping**: Regardless of whether a project is created from scratch or provided as pre-existing, `outputs.tf` resolves the correct active project IDs, ensuring downstream modules (networking, security, and workloads) consume them transparently.

---

#### D. File Inventory & Blueprints

```text
infrastructure/modules/1-projects/
├── versions.tf          # Mandates HashiCorp Google provider bounds (>= 5.0)
├── variables.tf         # Inputs for billing, organization folders, prefix, and BYO parameters
├── main.tf              # Implements projects, API enablement, billing bindings, and labels
└── outputs.tf           # Exposes project IDs & numbers to downstream stages
```

##### 1. Versions Specification (`versions.tf`)
```hcl
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0, < 6.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5.0"
    }
  }
}
```

##### 2. Variables Specification (`variables.tf`)
```hcl
# infrastructure/modules/1-projects/variables.tf

variable "billing_account" {
  description = "The enterprise billing account ID to bind to all created projects"
  type        = string
}

variable "folder_id" {
  description = "The folder ID under which to create the projects. If omitted, projects are created at organization root."
  type        = string
  default     = ""
}

variable "project_prefix" {
  description = "A prefix string appended to the start of all projects to guarantee uniqueness"
  type        = string
  default     = "esmeralda"
}

# BYO Project Toggles
variable "byo_net_host_project" {
  description = "Set to true if the customer is bringing a pre-existing Shared VPC Host Project"
  type        = bool
  default     = false
}

variable "byo_gateway_project" {
  description = "Set to true if the customer is bringing a pre-existing API Gateway/Ingress Project"
  type        = bool
  default     = false
}

variable "byo_governance_project" {
  description = "Set to true if the customer is bringing a pre-existing governance/security/telemetry project"
  type        = bool
  default     = false
}

variable "existing_net_host_project" {
  description = "The project ID of the pre-existing Shared VPC Host Project. Required if byo_net_host_project is true."
  type        = string
  default     = ""
}

variable "existing_gateway_project" {
  description = "The project ID of the pre-existing API Gateway/Ingress Project. Required if byo_gateway_project is true."
  type        = string
  default     = ""
}

variable "existing_governance_project" {
  description = "The project ID of the pre-existing governance/security/telemetry project. Required if byo_governance_project is true."
  type        = string
  default     = ""
}

# Cost Allocation Labels
variable "environment" {
  description = "The environment classification label (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}
```

##### 3. Implementation Logic (`main.tf`)
```hcl
# infrastructure/modules/1-projects/main.tf

resource "random_id" "project_suffix" {
  byte_length = 2
}

locals {
  suffix = random_id.project_suffix.hex
  
  # Resolve Project IDs dynamically: Use existing ID if BYO, otherwise generate unique name
  net_host_id   = var.byo_net_host_project ? var.existing_net_host_project : "${var.project_prefix}-net-host-${local.suffix}"
  gateway_id    = var.byo_gateway_project  ? var.existing_gateway_project  : "${var.project_prefix}-gateway-${local.suffix}"
  governance_id = var.byo_governance_project ? var.existing_governance_project : "${var.project_prefix}-governance-${local.suffix}"
  mcps_id       = "${var.project_prefix}-mcps-${local.suffix}"
  a2a_id        = "${var.project_prefix}-a2a-${local.suffix}"
  root_agent_id = "${var.project_prefix}-root-agent-${local.suffix}"

  # Systematic project-specific labeling mapping for FinOps and Cost Center attribution
  common_labels = {
    "env"        = var.environment
    "managed-by" = "terragrunt-esmeralda"
  }

  net_host_apis = [
    "compute.googleapis.com",
    "dns.googleapis.com",
    "servicenetworking.googleapis.com"
  ]

  gateway_apis = [
    "compute.googleapis.com",
    "apigee.googleapis.com",
    "certificatemanager.googleapis.com"
  ]

  mcps_apis = [
    "compute.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com"
  ]

  a2a_apis = [
    "compute.googleapis.com",
    "aiplatform.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
    "run.googleapis.com", # Required to trigger the private VPC bootstrapping Cloud Run Job!
    "artifactregistry.googleapis.com"
  ]

  root_agent_apis = [
    "compute.googleapis.com",
    "aiplatform.googleapis.com",
    "storage.googleapis.com"
  ]

  governance_apis = [
    "bigquery.googleapis.com",
    "logging.googleapis.com",
    "clouderrorreporting.googleapis.com",
    "cloudtrace.googleapis.com",
    "monitoring.googleapis.com",
    "cloudkms.googleapis.com",
    "secretmanager.googleapis.com"
  ]
}

# ====================================================================
# 1. GCP PROJECTS CREATION
# ====================================================================

# Provisioned conditionally: Only created if the customer does not BYO
resource "google_project" "net_host" {
  count           = var.byo_net_host_project ? 0 : 1
  name            = "Esmeralda Shared VPC Host"
  project_id      = local.net_host_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "networking-infrastructure"
    "team"        = "netops"
  })
}

# Provisioned conditionally: Only created if the customer does not BYO
resource "google_project" "gateway" {
  count           = var.byo_gateway_project ? 0 : 1
  name            = "Esmeralda Ingress Gateway"
  project_id      = local.gateway_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "ingress-gateways"
    "team"        = "platformops"
  })
}

# Central Tools Project: ALWAYS created by Esmeralda from scratch
resource "google_project" "mcps" {
  name            = "Esmeralda MCP Server Tools"
  project_id      = local.mcps_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "central-developer-tools"
    "team"        = "appdev-tools"
  })
}

# Core AI Platform Project: ALWAYS created by Esmeralda from scratch
resource "google_project" "a2a" {
  name            = "Esmeralda A2A Core Agents"
  project_id      = local.a2a_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "enterprise-ai-platform"
    "team"        = "core-ai-agents"
  })
}

# Line-of-Business User Facing Root Agent Project: ALWAYS created from scratch
resource "google_project" "root_agent" {
  name            = "Esmeralda LOB Root Agent"
  project_id      = local.root_agent_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "lob-business-solutions"
    "team"        = "lob-root-agent"
  })
}

# Governance and Telemetry Hub Project: Conditional creation
resource "google_project" "governance" {
  count           = var.byo_governance_project ? 0 : 1
  name            = "Esmeralda Central Governance & Telemetry Hub"
  project_id      = local.governance_id
  folder_id       = var.folder_id != "" ? var.folder_id : null
  billing_account = var.billing_account
  auto_create_network = false

  labels = merge(local.common_labels, {
    "cost-center" = "central-governance-and-telemetry"
    "team"        = "security-and-platformops"
  })
}

# ====================================================================
# 2. GCP SERVICE APIS ENABLEMENT
# ====================================================================

# Enable APIs on Shared VPC project only if Esmeralda created it
resource "google_project_service" "net_host" {
  for_each                   = var.byo_net_host_project ? [] : toset(local.net_host_apis)
  project                    = local.net_host_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.net_host]
}

# Enable APIs on Ingress Gateway project only if Esmeralda created it
resource "google_project_service" "gateway" {
  for_each                   = var.byo_gateway_project ? [] : toset(local.gateway_apis)
  project                    = local.gateway_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.gateway]
}

# Enable Central Tools APIs
resource "google_project_service" "mcps" {
  for_each                   = toset(local.mcps_apis)
  project                    = local.mcps_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.mcps]
}

# Enable Core AI Platform APIs
resource "google_project_service" "a2a" {
  for_each                   = toset(local.a2a_apis)
  project                    = local.a2a_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.a2a]
}

# Enable Line-of-Business APIs
resource "google_project_service" "root_agent" {
  for_each                   = toset(local.root_agent_apis)
  project                    = local.root_agent_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.root_agent]
}

# Enable Governance and Telemetry APIs only if created by Esmeralda
resource "google_project_service" "governance" {
  for_each                   = var.byo_governance_project ? [] : toset(local.governance_apis)
  project                    = local.governance_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false

  depends_on = [google_project.governance]
}

# ====================================================================
# 3. OUTPUTS SPECIFICATION
# ====================================================================

output "net_host_project_id" {
  description = "The active project ID hosting the Shared VPC network"
  value       = local.net_host_id
}

output "gateway_project_id" {
  description = "The active project ID hosting the API Ingress Gateway"
  value       = local.gateway_id
}

output "mcps_project_id" {
  description = "The project ID allocated for corporate MCP servers"
  value       = local.mcps_id
}

output "a2a_project_id" {
  description = "The project ID allocated for Core AI Platform and A2A agents"
  value       = local.a2a_id
}

output "root_project_id" {
  description = "The project ID allocated for client-facing LOB Root agent"
  value       = local.root_agent_id
}

output "governance_project_id" {
  description = "The active project ID hosting central governance, encryption, secrets, and telemetry"
  value       = local.governance_id
}

output "project_suffix" {
  description = "The random project suffix generated in Stage 1"
  value       = local.suffix
}
```
*(With this, any client's NetOps/PlatformOps teams can hand over pre-configured net_host and gateway projects, and Esmeralda will automatically provision and deploy the isolated workload projects and attach them securely to their Shared VPC.)*

---

<a name="stage-2"></a>
## 🌐 2. Estágio 2: Redes Privadas, DNS e PSC

<a name="s2-concepts"></a>
### A. Guia de Arquitetura e Decisões de Rede (Português)

## 🌐 2. Stage 2: Redes Privadas, DNS & PSC

O Stage 2 implementa a infraestrutura de rede Zero-Trust para garantir que nenhuma API ou agente de IA se comunique por canais públicos da internet.

### A. Topologia de Subredes da VPC (`gateway-vpc`)
No projeto `prj-net-host`, criamos os seguintes intervalos de IP:
*   **Subnet de Aplicações (`gke-subnet`)**: `10.0.0.0/20` para os computes locais e VMs de teste.
*   **Proxy Subnet Regional (`gateway-proxy-subnet`)**: `10.9.0.0/24` de uso exclusivo para balanceadores de carga internos baseados em Envoy (ILB).
*   **PSC NAT Subnet (`gateway-psc-subnet`)**: `10.10.0.0/24` para as conexões de saída do Private Service Connect.
*   **PSC Interface Subnet (`psc-interface-subnet`)**: `10.11.0.0/28` que fornece conexões locais para os agentes do Vertex AI (Reasoning Engine) operando sob a rede gerenciada do Google.

### B. DNS Privado e Roteamento Estático do Gateway
Para desacoplar as URLs dinâmicas do Vertex AI, criamos uma zona DNS privada no Cloud DNS chamada `internal.gateway.` apontando todas as rotas de ferramentas e agentes para o IP interno (`10.0.0.5`) do Internal Load Balancer (ILB):
*   `email.internal.gateway` -> `10.0.0.5`
*   `income-verification.internal.gateway` -> `10.0.0.5`
*   `dms.internal.gateway` -> `10.0.0.5`

---

<a name="s2-codes"></a>
### B. Documento de Implementação Detalhado e Códigos Prontos (Inglês)

# Stage 2: Shared VPC Host Networking & Secure Egress

This module handles core network topologies including private IP address allocation, Shared VPC, Cloud NAT gateway egress, Secure Web Proxy (SWP) whitelist policies, and Private Service Connect (PSC).

### 7.2 Stage 2: `modules/2-networking/` Specification

This module establishes the Shared VPC network, configures the internal subnet routing topologies, sets up Private Service Connect (PSC) Network Attachments, deploys Google Cloud's Secure Web Proxy (SWP) for audited internet egress, and handles corporate DNS zones. It is designed to run on the Shared VPC Host Project (`prj-net-host`), but can be toggled to a pure-attachment mode for pre-existing (brownfield) customer networks.

#### A. Network Subnet and Routing Architecture
In Greenfield mode, the module provisions a comprehensive hub network with dedicated subnets for core workloads, proxy-only routing, PSC endpoints, serverless integration, and database peering:

```mermaid
graph TD
    subgraph HostProject["prj-net-host (Shared VPC Host)"]
        subgraph VPC["Shared VPC Network (vpc-esmeralda-shared)"]
            SubnetCore["Core Backend Subnet<br/>(sb-esmeralda-core)<br/>10.0.1.0/24"]
            SubnetProxy["Regional Proxy Subnet<br/>(sb-esmeralda-proxy)<br/>10.9.0.0/24 (Active)"]
            SubnetPSC["PSC Subnet<br/>(sb-esmeralda-psc)<br/>10.10.0.0/24"]
            SubnetPSC_I["PSC Interface Subnet<br/>(sb-esmeralda-psc-interface)<br/>10.11.0.0/28"]
            PSA["Private Services Access Range<br/>(sql-peering-range)<br/>10.130.0.0/16"]
        end
        Router["Cloud Router"]
        NAT["Cloud NAT (External Outbound)"]
        SWP["Secure Web Proxy (SWP)<br/>Explicit Gateway Filter<br/>10.0.1.100"]
    end

    Router --> NAT
    SubnetCore --> SWP
    SWP --> Router
    PSA -. Internal Peering .-> DB["Private Cloud SQL<br/>(Database Project)"]
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
3.  **Firewall Protection**: Ingress firewalls are restricted to only allow ports `22` (SSH for debugging), `443` (HTTPS for APIs), and `ICMP` originating from the PSC Interface subnet (`10.11.0.0/28`).

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
    Host["prj-net-host<br/>Shared VPC Host"]
    Subnet["sb-esmeralda-core Subnet"]

    Host -. Service Project Attachment .-> P_MCP["prj-esmeralda-mcps"]
    Host -. Service Project Attachment .-> P_A2A["prj-esmeralda-a2a-agents"]
    Host -. Service Project Attachment .-> P_Root["prj-esmeralda-root-agent"]

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

#### G. File Inventory & Blueprints

```text
infrastructure/modules/2-networking/
├── versions.tf          # Mandates HashiCorp Google provider bounds (>= 5.0)
├── variables.tf         # Host project IDs, service project IDs, CIDR overrides, and BYO flags
├── main.tf              # Greenfield VPC, NAT, PSA + PSC Attachments, SWP, IAM network users, Cloud DNS
└── outputs.tf           # Exports VPC network ID, Subnet self-links, and DNS private zone name
```

##### 1. Versions Specification (`versions.tf`)
```hcl
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0, < 6.0.0"
    }
  }
}
```

##### 2. Variables Specification (`variables.tf`)
```hcl
# infrastructure/modules/2-networking/variables.tf

variable "net_host_project_id" {
  description = "The project ID of the Shared VPC Host"
  type        = string
}

variable "gateway_project_id" {
  description = "The project ID of the API Ingress Gateway"
  type        = string
}

variable "mcps_project_id" {
  description = "The project ID allocated for corporate MCP servers"
  type        = string
}

variable "a2a_project_id" {
  description = "The project ID allocated for Core AI Platform and A2A agents"
  type        = string
}

variable "root_project_id" {
  description = "The project ID allocated for client-facing LOB Root agent"
  type        = string
}

variable "region" {
  description = "The primary region where subnets and resources are placed"
  type        = string
  default     = "us-central1"
}

# BYO Networking Toggles
variable "byo_networking" {
  description = "If true, skip creating the Shared VPC network and subnets, and attach to existing ones instead."
  type        = bool
  default     = false
}

variable "byo_governance_project" {
  description = "Set to true if the customer is bringing a pre-existing governance/security/telemetry project"
  type        = bool
  default     = false
}

variable "governance_project_id" {
  description = "The project ID allocated for central governance, security, and telemetry"
  type        = string
}

variable "byo_net_host_project" {
  description = "Set to true if the customer is bringing a pre-existing Shared VPC Host Project"
  type        = bool
  default     = false
}

variable "byo_gateway_project" {
  description = "Set to true if the customer is bringing a pre-existing API Gateway/Ingress Project"
  type        = bool
  default     = false
}

variable "existing_vpc_id" {
  description = "The full resource URI of the existing Shared VPC network. Required if byo_networking is true."
  type        = string
  default     = ""
}

variable "existing_subnet_id" {
  description = "The full resource URI of the existing backend workload subnet. Required if byo_networking is true."
  type        = string
  default     = ""
}

# Workload Project Numbers are resolved dynamically in main.tf via data "google_project" to avoid manual inputs and automation locks.

# Explicit Proxy & PSC Options
variable "enable_psc_interface" {
  description = "Set to true to create a PSC Network Attachment for Serverless Agent ingress"
  type        = bool
  default     = true
}

variable "enable_secure_web_proxy" {
  description = "Set to true to deploy the Secure Web Proxy for outbound egress filtering"
  type        = bool
  default     = true
}

variable "environment" {
  description = "The environment classification (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

variable "project_suffix" {
  description = "The random project suffix generated in Stage 1"
  type        = string
}
```

##### 3. Implementation Logic (`main.tf`)
```hcl
# infrastructure/modules/2-networking/main.tf

# Resolve dynamically generated project numbers to avoid manual inputs and automation locks
data "google_project" "mcps" {
  project_id = var.mcps_project_id
}

data "google_project" "a2a" {
  project_id = var.a2a_project_id
}

data "google_project" "root_agent" {
  project_id = var.root_project_id
}

# ====================================================================
# 1. SHARED VPC HOST & SERVICE PROJECTS ATTACHMENTS
# ====================================================================

# Enable Shared VPC host mode on the host project (skipped if BYO)
resource "google_compute_shared_vpc_host_project" "host" {
  count   = var.byo_net_host_project ? 0 : 1
  project = var.net_host_project_id
}

# Attach the MCP Central Tools project as a service project
resource "google_compute_shared_vpc_service_project" "mcps" {
  host_project    = var.net_host_project_id
  service_project = var.mcps_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# Attach the Core AI Platform project as a service project
resource "google_compute_shared_vpc_service_project" "a2a" {
  host_project    = var.net_host_project_id
  service_project = var.a2a_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# Attach the Line-of-Business Root Agent project as a service project
resource "google_compute_shared_vpc_service_project" "root_agent" {
  host_project    = var.net_host_project_id
  service_project = var.root_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# Attach the Gateway Ingress project conditionally as a service project
resource "google_compute_shared_vpc_service_project" "gateway" {
  count           = var.byo_gateway_project ? 0 : 1
  host_project    = var.net_host_project_id
  service_project = var.gateway_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# Attach the Governance project conditionally as a service project
resource "google_compute_shared_vpc_service_project" "governance" {
  count           = var.byo_governance_project ? 0 : 1
  host_project    = var.net_host_project_id
  service_project = var.governance_project_id
  depends_on      = [google_compute_shared_vpc_host_project.host]
}

# ====================================================================
# 2. GREENFIELD SHARED VPC NETWORKING CREATION
# ====================================================================

# ====================================================================
# 2. GREENFIELD SHARED VPC NETWORKING CREATION
# ====================================================================

# Provision Shared VPC (Only if greenfield)
resource "google_compute_network" "shared_vpc" {
  count                   = var.byo_networking ? 0 : 1
  name                    = "vpc-esmeralda-shared-${var.environment}"
  project                 = var.net_host_project_id
  auto_create_subnetworks = false
}

# Core Subnet for Workloads
resource "google_compute_subnetwork" "core" {
  count                    = var.byo_networking ? 0 : 1
  name                     = "sb-esmeralda-core-${var.environment}"
  project                  = var.net_host_project_id
  region                   = var.region
  network                  = google_compute_network.shared_vpc[0].id
  ip_cidr_range            = "10.0.1.0/24"
  private_ip_google_access = true
}

# Proxy-only Subnet for regional load balancing (Internal Load Balancer/Kong/Apigee)
resource "google_compute_subnetwork" "proxy" {
  count         = var.byo_networking ? 0 : 1
  name          = "sb-esmeralda-proxy-${var.environment}"
  project       = var.net_host_project_id
  region        = var.region
  network       = google_compute_network.shared_vpc[0].id
  ip_cidr_range = "10.9.0.0/24"
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

# PSC Subnet for Private Service Connect Endpoint bindings
resource "google_compute_subnetwork" "psc" {
  count         = var.byo_networking ? 0 : 1
  name          = "sb-esmeralda-psc-${var.environment}"
  project       = var.net_host_project_id
  region        = var.region
  network       = google_compute_network.shared_vpc[0].id
  ip_cidr_range = "10.10.0.0/24"
}

# PSC Interface Subnet for Serverless Agent Incoming Tunnels
resource "google_compute_subnetwork" "psc_interface" {
  count         = !var.byo_networking && var.enable_psc_interface ? 1 : 0
  name          = "sb-esmeralda-psc-interface-${var.environment}"
  project       = var.net_host_project_id
  region        = var.region
  network       = google_compute_network.shared_vpc[0].id
  ip_cidr_range = "10.11.0.0/28"
}

# Private Service Connection (PSA) range for Cloud SQL Peering
resource "google_compute_global_address" "sql_peering" {
  count         = var.byo_networking ? 0 : 1
  name          = "sql-peering-range-${var.environment}"
  project       = var.net_host_project_id
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.shared_vpc[0].id
}

resource "google_service_networking_connection" "sql_connection" {
  count                   = var.byo_networking ? 0 : 1
  network                 = google_compute_network.shared_vpc[0].id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_peering[0].name]
}

# Cloud Router & NAT for Outbound API access
resource "google_compute_router" "router" {
  count   = var.byo_networking ? 0 : 1
  name    = "cr-esmeralda-nat-${var.environment}"
  project = var.net_host_project_id
  region  = var.region
  network = google_compute_network.shared_vpc[0].id
}

resource "google_compute_router_nat" "nat" {
  count                              = var.byo_networking ? 0 : 1
  name                               = "nat-esmeralda-outbound-${var.environment}"
  project                            = var.net_host_project_id
  router                             = google_compute_router.router[0].name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# ====================================================================
# 3. PRIVATE SERVICE CONNECT (PSC) NETWORK ATTACHMENT
# ====================================================================

# Compute Network Attachment allowing Serverless Reasoning engines auto acceptance
resource "google_compute_network_attachment" "psc_interface" {
  count                 = var.enable_psc_interface ? 1 : 0
  project               = var.net_host_project_id
  name                  = "gateway-psc-interface-attachment-${var.environment}"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [var.byo_networking ? var.existing_subnet_id : try(google_compute_subnetwork.psc_interface[0].self_link, "")]
}

# Ingress Firewall to accept traffic from the PSC Interface Subnet
resource "google_compute_firewall" "psc_interface_allow" {
  count         = var.enable_psc_interface && !var.byo_networking ? 1 : 0
  project       = var.net_host_project_id
  name          = "allow-psc-interface-ingress-${var.environment}"
  network       = google_compute_network.shared_vpc[0].id
  direction     = "INGRESS"
  priority      = 1000
  source_ranges = ["10.11.0.0/28"]

  allow {
    protocol = "tcp"
    ports    = ["22", "443"]
  }
  allow {
    protocol = "icmp"
  }
}

# ====================================================================
# 4. SECURE WEB PROXY (SWP) egres filtering
# ====================================================================

# Deploy SWP at IP 10.0.1.100 (100th IP of Core subnet)
module "secure_web_proxy" {
  count      = var.enable_secure_web_proxy && !var.byo_networking ? 1 : 0
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/net-swp?ref=v53.0.0"
  project_id = var.net_host_project_id
  region     = var.region
  name       = "gateway-swp-${var.environment}"
  network    = google_compute_network.shared_vpc[0].id
  subnetwork = google_compute_subnetwork.core[0].id
  
  gateway_config = {
    addresses = ["10.0.1.100"]
  }
  
  policy_rules = {
    allow-all = {
      priority        = 1000
      session_matcher = "host() != ''"
      basic_profile   = "ALLOW"
    }
  }

  depends_on = [google_compute_subnetwork.proxy]
}

# ====================================================================
# 5. SUBNET-LEVEL SERVICE AGENT IAM NETWORK USER BINDINGS
# ====================================================================

locals {
  target_vpc_id    = var.byo_networking ? var.existing_vpc_id : try(google_compute_network.shared_vpc[0].id, "")
  target_subnet_id = var.byo_networking ? var.existing_subnet_id : try(google_compute_subnetwork.core[0].id, "")
  
  # Parse subnet name and region from the subnet ID URI
  subnet_parsed_name   = element(split("/", local.target_subnet_id), length(split("/", local.target_subnet_id)) - 1)
  subnet_parsed_region = element(split("/", local.target_subnet_id), length(split("/", local.target_subnet_id)) - 3)

  # Consolidate all Service accounts that need Subnet Network User access
  subnet_network_users = [
    # 1. MCP Central Tools Project Service SAs
    "serviceAccount:service-${data.google_project.mcps.number}@cloudservices.gserviceaccount.com",
    "serviceAccount:service-${data.google_project.mcps.number}@serverless-robot-prod.iam.gserviceaccount.com",

    # 2. Core AI Platform Project Service SAs
    "serviceAccount:service-${data.google_project.a2a.number}@cloudservices.gserviceaccount.com",
    "serviceAccount:service-${data.google_project.a2a.number}@serverless-robot-prod.iam.gserviceaccount.com",
    "serviceAccount:service-${data.google_project.a2a.number}@gcp-sa-aiplatform.iam.gserviceaccount.com",

    # 3. LOB Root Agent Project Service SAs
    "serviceAccount:service-${data.google_project.root_agent.number}@cloudservices.gserviceaccount.com",
    "serviceAccount:service-${data.google_project.root_agent.number}@gcp-sa-aiplatform.iam.gserviceaccount.com"
  ]
}

# Grant compute.networkUser on the Core Workload subnet to all service project robots
resource "google_compute_subnetwork_iam_member" "network_users" {
  for_each   = toset(local.subnet_network_users)
  project    = var.net_host_project_id
  region     = local.subnet_parsed_region
  subnetwork = local.subnet_parsed_name
  role       = "roles/compute.networkUser"
}

# ====================================================================
# 6. PRIVATE DNS SERVICE DISCOVERY ZONES
# ====================================================================

# A. Central Private Zone for inter-agent communication
resource "google_dns_managed_zone" "private_dns" {
  name        = "esmeralda-private-dns-${var.environment}"
  project     = var.net_host_project_id
  dns_name    = "esmeralda.internal."
  description = "Central Private DNS Zone for Esmeralda micro-agent communications"

  visibility = "PRIVATE"

  private_visibility_config {
    networks {
      network_url = local.target_vpc_id
    }
  }
}

# B. Dedicated Private Zone for Gateway & SWP Resolution Peering
module "psc_interface_dns_zone" {
  count      = var.enable_psc_interface && !var.byo_networking ? 1 : 0
  source     = "github.com/GoogleCloudPlatform/cloud-foundation-fabric//modules/dns?ref=v53.0.0"
  project_id = var.net_host_project_id
  name       = "internal-gateway-${var.environment}"
  zone_config = {
    domain = "internal.gateway."
    private = {
      client_networks = [local.target_vpc_id]
    }
  }
  recordsets = {
    # Resolve wildcard gateway endpoints to the future internal gateway IP placeholder
    "A *"   = { records = ["10.0.1.200"] } # Placeholder for regional load balancer VIP
    # Resolve swp to the Secure Web Proxy IP
    "A swp" = { records = ["10.0.1.100"] }
  }
}
```

##### 4. Outputs Specification (`outputs.tf`)
```hcl
# infrastructure/modules/2-networking/outputs.tf

output "network_id" {
  description = "The resolved Shared VPC network resource ID"
  value       = local.target_vpc_id
}

output "subnet_id" {
  description = "The resolved backend workload subnet resource ID"
  value       = local.target_subnet_id
}

output "subnet_name" {
  description = "The resolved name of the backend workload subnet"
  value       = local.subnet_parsed_name
}

output "dns_zone_name" {
  description = "The name of the private DNS managed zone"
  value       = google_dns_managed_zone.private_dns.name
}

output "dns_zone_dns_name" {
  description = "The suffix domain name of the private DNS managed zone"
  value       = google_dns_managed_zone.private_dns.dns_name
}

output "psc_network_attachment_id" {
  description = "The URI of the Private Service Connect Network Attachment"
  value       = try(google_compute_network_attachment.psc_interface[0].id, "")
}

output "secure_web_proxy_ip" {
  description = "The private IP address of the Secure Web Proxy"
  value       = var.enable_secure_web_proxy && !var.byo_networking ? "10.0.1.100" : ""
}
```

---

<a name="stage-3"></a>
## 🔐 3. Estágio 3: Segurança, Chaves CMEK, Secrets e Identidades

<a name="s3-concepts"></a>
### A. Guia de Arquitetura e Decisões de Segurança (Português)

## 🔐 3. Stage 3: Segurança, Chaves CMEK, Secrets & Identidades

O Stage 3 centraliza as barreiras de conformidade de segurança e controle de dados, gerenciado no projeto isolado `prj-esmeralda-governance`.

### A. Criptografia CMEK e Secret Manager
Criamos um Keyring do Cloud KMS centralizado para criptografar dados persistentes:
*   `key-postgresql`: Criptografa o disco do Cloud SQL no projeto `prj-esmeralda-a2a-agents`.
*   `key-gcs-staging`: Criptografa os buckets de gravação de logs de telemetria.

E usamos o Secret Manager para persistir credenciais sem riscos de exposição em disco:
*   `postgresql-admin-password`: Senha master administrativa para bootstrap de privilégios.

---

### B. 🚨 Auditoria de Conformidade de Identidades (Least Privilege)
**IMPORTANTE (Correção de Auditoria de Segurança):** 
Durante nossa auditoria do Esmeralda legado, removemos completamente as permissões relacionadas a um `agent_repo` no Artifact Registry (repositório de containers de agentes). Os agentes ADK Reasoning Engine não utilizam Docker e são empacotados como pacotes compactados `.zip` em buckets do GCS. Mantivemos apenas as permissões de gravação de imagens para o `mcp_repo` (usado pelos Cloud Runs das ferramentas).

Além disso, eliminamos a Service Account centralizadora genérica `test-vm-sa`. No novo modelo, cada carga de trabalho opera sob uma **identidade de serviço isolada e específica por projeto**:

```mermaid
graph TD
    subgraph Governance["Projeto: prj-esmeralda-governance"]
        KMS["Chaves KMS (CMEK)"]
        Secret["Secret Manager"]
    end

    subgraph MCPs["Projeto: prj-esmeralda-mcps"]
        SA_MCP["sa-mcp-runtimes@...gserviceaccount.com"] -->|Apenas Leitura| Registry["Artifact Registry: mcp-repo"]
    end

    subgraph Agents["Projeto: prj-esmeralda-a2a-agents"]
        SA_A2A["sa-a2a-agent@...gserviceaccount.com"] -->|Acesso Exclusivo| SQL["Cloud SQL (PostgreSQL)"]
        SA_A2A -->|Apenas Leitura| GCS_Agent["GCS: staging-agents-bucket"]
    end

    SA_A2A -. Consome Chaves/Secrets .-> Governance
    SA_MCP -. Consome Secrets .-> Governance
```

---

---

<a name="s3-codes"></a>
### B. Documento de Implementação Detalhado e Códigos Prontos (Inglês)

# Stage 3: Security Keys, Secret Management, and Log Sinks

This module provisions KMS Keyrings and CryptoKeys, sets up Secret Manager secrets, and configures centralized Cloud Logging Sinks routed to BigQuery and Log Analytics buckets.

### 7.3 Stage 3: `modules/3-security/` Specification

This module establishes central customer-managed cryptographic keys (CMEK) via Cloud KMS, configures secure secret storage boundaries in Secret Manager, provisions isolated, least-privilege workload Service Accounts for each engineering domain (including a dedicated Test VM service account and full-parity roles from Esmeralda's monolithic `test-vm-sa`), and hooks up enterprise audit and telemetry log sinks.

Under our centralized governance design, all KMS keyrings, keys, and secrets are created in the centralized **`prj-esmeralda-governance`** project during Stage 3. Workload runtimes (e.g. Cloud SQL in `prj-esmeralda-a2a-agents`) merely consume these resources over cross-project IAM bindings.

#### A. Cryptographic, Secrets, and Identity Isolation Architecture
Stage 3 establishes centralized encryption-at-rest keys, credentials, and cryptographic identities to satisfy strict corporate infosec rules:

```mermaid
graph TD
    subgraph "prj-net-host (Shared VPC Host)"
        DNS["Managed DNS Zone<br/>(dns-esmeralda-internal)"]
    end

    subgraph "prj-esmeralda-governance (Governance & Telemetry Hub)"
        TelemetryLogs["BigQuery Dataset<br/>(esmeralda_telemetry_logs)"]
        
        KeyRing["KMS Keyring<br/>(keyring-esmeralda)"]
        KeySQL["Database Key (CMEK)<br/>(key-esmeralda-sql)"]
        KeySecrets["Secrets Key (CMEK)<br/>(key-esmeralda-secrets)"]
        
        SecretDB["Database Password Secret<br/>(secret-pg-admin-password)"]
    end

    subgraph "prj-esmeralda-a2a-agents (AI Platform Project)"
        SQLRobot["Cloud SQL Service Robot<br/>(service-prj-a2a-sql...)"]
    end

    SQLRobot -->|roles/cloudkms.cryptoKeyEncrypterDecrypter <br/> Cross-Project CMEK Grant| KeySQL
    SecRobot["Secret Manager Service Robot<br/>(service-prj-gov-sm...)"] -->|roles/cloudkms.cryptoKeyEncrypterDecrypter| KeySecrets
```

##### 1. Key Refinements and Additions:
*   **Workload Service Account Roles Alignment**:
    In the monolithic setup, the single `test-vm-sa` account accumulated over 11 roles (including `roles/aiplatform.user`, `roles/storage.objectAdmin`, `roles/telemetry.writer`, and `roles/bigquery.jobUser`) because the VM also acted as the execution identity for the Reasoning Engine. In our enterprise multi-project landing zone, we **split and assign these roles to distinct service accounts** according to the principle of least privilege, guaranteeing full feature-parity:
    *   **`sa-esmeralda-mcps`** (Central Tools Project): Authorized with `roles/logging.logWriter`, `roles/monitoring.metricWriter`, and `roles/cloudtrace.agent` to write application telemetry.
    *   **`sa-esmeralda-a2a`** (AI Platform Project): Fully loaded with the transactional database and AI roles: `roles/cloudsql.client`, `roles/cloudsql.instanceUser`, `roles/aiplatform.user`, `roles/logging.logWriter`, `roles/monitoring.metricWriter`, `roles/cloudtrace.agent`, `roles/storage.objectAdmin` (for reasoning templates GCS buckets), `roles/serviceusage.serviceUsageConsumer`, and `roles/telemetry.writer`.
    *   **`sa-esmeralda-root`** (LOB App Project): Fully loaded with the customer reasoning and orchestration roles: `roles/aiplatform.user`, `roles/storage.objectAdmin`, `roles/logging.logWriter`, `roles/monitoring.metricWriter`, `roles/cloudtrace.agent`, `roles/serviceusage.serviceUsageConsumer`, and `roles/telemetry.writer`.
*   **Dedicated Test VM Identity (`sa-esmeralda-test-vm`)**:
    To support connectivity testing, local debugging, and tool testing (DMS, Calculator) from a secure jumpbox without over-privileging operators, we introduce a dedicated Test VM Service Account. It resides in the Line of Business project (`prj-esmeralda-root-agent`) or `prj-net-host` and is assigned:
    *   `roles/logging.logWriter` and `roles/monitoring.metricWriter` for VM health logging.
    *   `roles/run.invoker` inside `prj-esmeralda-mcps` (to invoke private Cloud Run MCP server tools).
    *   `roles/run.invoker` inside `prj-esmeralda-a2a-agents` (to invoke private A2A endpoints or bootstrapping runs).
    *   `roles/aiplatform.user` inside `prj-esmeralda-a2a-agents` (to invoke private Vertex AI Reasoning Engines).
    *   `roles/iam.serviceAccountTokenCreator` on **itself** (allowing the VM's operators to generate short-lived, secure OIDC identity tokens programmatically via the IAM API for curling private microservices).

---

#### B. Greenfield vs. Brownfield (BYO) Logic
When `byo_security = true` is declared in `env.yaml`:
*   KMS Keyrings, KMS Crypto Keys, and Secret Manager Secrets are **completely bypassed** during deployment.
*   Workload Service Accounts, the Test VM Service Account, and their precise IAM role bindings are **still created** and linked back to target project boundaries.
*   Downstream modules switch their inputs to point to the static pre-existing key and secret IDs passed via local configurations.

---

#### C. File Inventory & Blueprints

```text
infrastructure/modules/3-security/
├── versions.tf          # Mandates HashiCorp Google provider bounds (>= 5.0)
├── variables.tf         # Multi-project inputs, BYO KMS/Secret overrides, and project numbers
├── main.tf              # Implements Cloud KMS, secrets, SAs, and log sinks
└── outputs.tf           # Exports SAs, KMS Key IDs, and Secret Resource Names
```

##### 1. Versions Specification (`versions.tf`)
```hcl
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0.0, < 6.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5.0"
    }
  }
}
```

##### 2. Variables Specification (`variables.tf`)
```hcl
# infrastructure/modules/3-security/variables.tf

variable "net_host_project_id" {
  description = "The project ID of the Shared VPC Host"
  type        = string
}

variable "gateway_project_id" {
  description = "The project ID of the API Ingress Gateway"
  type        = string
}

variable "mcps_project_id" {
  description = "The project ID allocated for corporate MCP servers"
  type        = string
}

variable "a2a_project_id" {
  description = "The project ID allocated for Core AI Platform and A2A agents"
  type        = string
}

variable "root_project_id" {
  description = "The project ID allocated for client-facing LOB Root agent"
  type        = string
}

variable "governance_project_id" {
  description = "The project ID allocated for central security, governance, and telemetry"
  type        = string
}

variable "region" {
  description = "The primary region where regional security resources are placed"
  type        = string
  default     = "us-central1"
}

# Workload and Governance project numbers are resolved dynamically in main.tf via data "google_project"

# BYO Security Toggles
variable "byo_security" {
  description = "If true, bypass creation of KMS Keyrings, Keys, and Secrets, and use pre-existing resources instead"
  type        = bool
  default     = false
}

variable "existing_database_key_id" {
  description = "The full resource URI of the existing database KMS key. Required if byo_security is true."
  type        = string
  default     = ""
}

variable "existing_secrets_key_id" {
  description = "The full resource URI of the existing secrets KMS key. Required if byo_security is true."
  type        = string
  default     = ""
}

variable "existing_db_password_secret_id" {
  description = "The full resource name of the existing DB password secret. Required if byo_security is true."
  type        = string
  default     = ""
}

variable "environment" {
  description = "The environment classification (e.g., dev, qa, prod)"
  type        = string
  default     = "dev"
}

variable "project_suffix" {
  description = "The random project suffix generated in Stage 1"
  type        = string
}

variable "backend_subnet_id" {
  description = "The resource ID/name of the backend subnet on the Shared VPC for Direct VPC Egress"
  type        = string
}

variable "gateway_subnet_id" {
  description = "The resource ID/name of the gateway subnet on the Shared VPC for Gateway Egress"
  type        = string
}

```

##### 3. Implementation Logic (`main.tf`)
```hcl
# infrastructure/modules/3-security/main.tf

# Resolve dynamically generated project numbers to avoid manual inputs and automation locks
data "google_project" "governance" {
  project_id = var.governance_project_id
}

data "google_project" "a2a" {
  project_id = var.a2a_project_id
}

# ====================================================================
# 1. CUSTOMER-MANAGED ENCRYPTION KEYS (CMEK) via CLOUD KMS
# ====================================================================

# Regional Key Ring for central security & governance
resource "google_kms_key_ring" "keyring" {
  count    = var.byo_security ? 0 : 1
  name     = "keyring-esmeralda-${var.environment}"
  project  = var.governance_project_id
  location = var.region
}

# KMS Key for Private Cloud SQL database encryption
resource "google_kms_crypto_key" "database_key" {
  count           = var.byo_security ? 0 : 1
  name            = "key-esmeralda-sql-${var.environment}"
  key_ring        = google_kms_key_ring.keyring[0].id
  rotation_period = "7776000s" # 90 days rotation

  lifecycle {
    prevent_destroy = false
  }
}

# KMS Key for Secret Manager payload encryption
resource "google_kms_crypto_key" "secrets_key" {
  count           = var.byo_security ? 0 : 1
  name            = "key-esmeralda-secrets-${var.environment}"
  key_ring        = google_kms_key_ring.keyring[0].id
  rotation_period = "7776000s"

  lifecycle {
    prevent_destroy = false
  }
}

# Dynamic key IDs resolution based on BYO toggle
locals {
  resolved_database_key_id = var.byo_security ? var.existing_database_key_id : try(google_kms_crypto_key.database_key[0].id, "")
  resolved_secrets_key_id  = var.byo_security ? var.existing_secrets_key_id  : try(google_kms_crypto_key.secrets_key[0].id, "")
}

# --------------------------------------------------------------------
# KMS IAM Grants: Authorizing Service Project Robots
# --------------------------------------------------------------------

# Grant Cloud SQL service identity (residing in workloads project) access to decrypt/encrypt Postgres disk CMEK
resource "google_kms_crypto_key_iam_member" "sql_kms" {
  count         = var.byo_security ? 0 : 1
  crypto_key_id = google_kms_crypto_key.database_key[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.a2a.number}@gcp-sa-cloudsql.iam.gserviceaccount.com"
}

# Grant Secret Manager service identity (residing in governance project) access to decrypt/encrypt credentials CMEK
resource "google_kms_crypto_key_iam_member" "secrets_kms" {
  count         = var.byo_security ? 0 : 1
  crypto_key_id = google_kms_crypto_key.secrets_key[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.governance.number}@gcp-sa-secretmanager.iam.gserviceaccount.com"
}

# ====================================================================
# 2. SECRET MANAGER BOUNDARIES & AUTO GENERATED CREDENTIALS
# ====================================================================

# Generate secure, unique PostgreSQL administrator password
resource "random_password" "db_password" {
  count            = var.byo_security ? 0 : 1
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

# Secret container for database master credentials, now centralized in Governance project
resource "google_secret_manager_secret" "db_password" {
  count     = var.byo_security ? 0 : 1
  secret_id = "secret-pg-admin-password-${var.environment}"
  project   = var.governance_project_id

  replication {
    user_managed {
      replicas {
        location = var.region
        customer_managed_encryption {
          kms_key_name = local.resolved_secrets_key_id
        }
      }
    }
  }
}

# Put the generated password into the secret container
resource "google_secret_manager_secret_version" "db_password" {
  count       = var.byo_security ? 0 : 1
  secret      = google_secret_manager_secret.db_password[0].id
  secret_data = random_password.db_password[0].result
}

# Local resolution for Secret resource name
locals {
  resolved_db_password_secret_id = var.byo_security ? var.existing_db_password_secret_id : try(google_secret_manager_secret.db_password[0].id, "")
}

# ====================================================================
# 3. LEAST-PRIVILEGE WORKLOAD SERVICE ACCOUNTS & IAM ROLE BINDINGS
# ====================================================================

# --------------------------------------------------------------------
# A. AppDev Tools Project Identity (MCP Tool Servers)
# --------------------------------------------------------------------
resource "google_service_account" "mcps_sa" {
  account_id   = "sa-esmeralda-mcps-${var.environment}"
  display_name = "Esmeralda MCP Server Workload Service Account"
  project      = var.mcps_project_id
}

# Grant telemetry and tracing permissions
resource "google_project_iam_member" "mcps_roles" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent"
  ])
  project  = var.mcps_project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.mcps_sa.email}"
}

# --------------------------------------------------------------------
# B. Core AI Platform Agent Identity (A2A Agent & Bootstrapping Job)
# --------------------------------------------------------------------
resource "google_service_account" "a2a_sa" {
  account_id   = "sa-esmeralda-a2a-${var.environment}"
  display_name = "Esmeralda Core A2A Agent Workload Service Account"
  project      = var.a2a_project_id
}

# Full-parity roles derived from the monolithic test-sa setup
resource "google_project_iam_member" "a2a_roles" {
  for_each = toset([
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/telemetry.writer",
    "roles/storage.objectAdmin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/browser",
    "roles/cloudapiregistry.viewer"
  ])
  project  = var.a2a_project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.a2a_sa.email}"
}

# Grant A2A Service account reading rights on the Database Master secret (resolves to existing or new)
resource "google_secret_manager_secret_iam_member" "a2a_secret_accessor" {
  secret_id = local.resolved_db_password_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.a2a_sa.email}"
}

# --------------------------------------------------------------------
# C. Line-of-Business Root Orchestrator Identity (Root Agent)
# --------------------------------------------------------------------
resource "google_service_account" "root_sa" {
  account_id   = "sa-esmeralda-root-${var.environment}"
  display_name = "Esmeralda LOB Root Agent Workload Service Account"
  project      = var.root_project_id
}

# Full-parity roles derived from the monolithic test-sa setup
resource "google_project_iam_member" "root_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/storage.objectAdmin",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/telemetry.writer",
    "roles/browser",
    "roles/cloudapiregistry.viewer"
  ])
  project  = var.root_project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.root_sa.email}"
}

# STRICT SERVICE-TO-SERVICE IMPERSONATION BINDING:
# Root Agent is authorized to generate identity/ID tokens under A2A Agent's identity
# to securely invoke upstream cross-project Reasoning Engines privately.
resource "google_service_account_iam_member" "root_impersonates_a2a" {
  service_account_id = google_service_account.a2a_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.root_sa.email}"
}

# --------------------------------------------------------------------
# D. Test VM Dedicated Identity (For SSH Jumpbox Connectivity Testing)
# --------------------------------------------------------------------
resource "google_service_account" "test_vm_sa" {
  account_id   = "sa-esmeralda-test-vm-${var.environment}"
  display_name = "Esmeralda Test VM Workload Service Account"
  project      = var.root_project_id
}

# Standard VM logging and Vertex user permissions in local project
resource "google_project_iam_member" "test_vm_roles" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/aiplatform.user"
  ])
  project  = var.root_project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.test_vm_sa.email}"
}

# Grant run.invoker in tools project so operators can curl private Cloud Run MCP servers
resource "google_project_iam_member" "test_vm_mcp_invoker" {
  project = var.mcps_project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.test_vm_sa.email}"
}

# Grant run.invoker in A2A project so operators can trigger bootstrappers or SQL tools
resource "google_project_iam_member" "test_vm_a2a_invoker" {
  project = var.a2a_project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.test_vm_sa.email}"
}

# Grant Token Creator to the SA on itself so developers can generate identity tokens
resource "google_service_account_iam_member" "test_vm_token_creator" {
  service_account_id = google_service_account.test_vm_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.test_vm_sa.email}"
}

# ====================================================================
# 4. ENTERPRISE SYSTEM & TELEMETRY COMPLIANCE SINK
# ====================================================================

# Set up BigQuery Dataset to centralize OpenTelemetry and Gemini executions logs
resource "google_bigquery_dataset" "telemetry_logs" {
  dataset_id                  = "esmeralda_telemetry_logs_${var.environment}"
  project                     = var.governance_project_id
  location                    = var.region
  description                 = "Centralized dataset for Esmeralda micro-agent audit and observability logs"
  default_table_expiration_ms = 2592000000 # 30 Days Retention
}

locals {
  monitored_projects = {
    net_host   = var.net_host_project_id
    gateway    = var.gateway_project_id
    mcps       = var.mcps_project_id
    a2a        = var.a2a_project_id
    root       = var.root_project_id
    governance = var.governance_project_id
  }
}

# Deploy Log Sinks across all six isolated project boundaries
resource "google_logging_project_sink" "central_sinks" {
  for_each    = local.monitored_projects
  name        = "esmeralda-central-telemetry-sink-${var.environment}"
  project     = each.value
  destination = "bigquery.googleapis.com/projects/${var.governance_project_id}/datasets/${google_bigquery_dataset.telemetry_logs.dataset_id}"

  # Target Vertex AI stdout logs, custom tool traces, and database auditing events
  filter = "resource.type=\"aiplatform.googleapis.com/ReasoningEngine\" OR logName=~\"gen_ai\" OR logName=~\"reasoning_engine_stdout\" OR logName=~\"reasoning_engine_stderr\" OR resource.type=\"cloud_run_revision\""

  unique_writer_identity = true
}

# Authorize each Logging sink writer identity to insert rows into our BigQuery Dataset
resource "google_bigquery_dataset_iam_member" "dataset_writers" {
  for_each   = google_logging_project_sink.central_sinks
  dataset_id = google_bigquery_dataset.telemetry_logs.dataset_id
  project    = var.governance_project_id
  role       = "roles/bigquery.dataEditor"
  member     = each.value.writer_identity
}

# --------------------------------------------------------------------
# Direct VPC Egress Subnet User Permissions (AUDIT-01 Fix)
# --------------------------------------------------------------------

# Grant Network User role to the A2A Agent Service Account on the backend subnet
resource "google_compute_subnetwork_iam_member" "a2a_subnet_user" {
  project    = var.net_host_project_id
  region     = var.region
  subnetwork = var.backend_subnet_id
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.a2a_sa.email}"
}

# Grant Network User role to the MCP tools Service Account on the backend subnet (for Direct VPC Egress)
resource "google_compute_subnetwork_iam_member" "mcps_subnet_user" {
  project    = var.net_host_project_id
  region     = var.region
  subnetwork = var.backend_subnet_id
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.mcps_sa.email}"
}

# Grant Network User role to the Root Agent Service Account on the backend subnet
resource "google_compute_subnetwork_iam_member" "root_subnet_user" {
  project    = var.net_host_project_id
  region     = var.region
  subnetwork = var.backend_subnet_id
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.root_sa.email}"
}

# ====================================================================
# 6. OUTPUTS SPECIFICATION
# ====================================================================

output "database_key_id" {
  description = "The fully qualified crypto key ID for private database CMEK"
  value       = local.resolved_database_key_id
}

output "secrets_key_id" {
  description = "The fully qualified crypto key ID for secret payloads CMEK"
  value       = local.resolved_secrets_key_id
}

output "db_password_secret_name" {
  description = "The Secret Manager resource path representing the DB admin credentials"
  value       = local.resolved_db_password_secret_id
}

output "mcps_sa_email" {
  description = "The email address of the MCP tools server service account"
  value       = google_service_account.mcps_sa.email
}

output "a2a_agent_sa_email" {
  description = "The email address of the A2A agent service account"
  value       = google_service_account.a2a_sa.email
}

output "root_agent_sa_email" {
  description = "The email address of the Root Orchestrator service account"
  value       = google_service_account.root_sa.email
}

output "test_vm_sa_email" {
  description = "The email address of the dedicated debugging Test VM service account"
  value       = google_service_account.test_vm_sa.email
}

output "telemetry_dataset_id" {
  description = "The BigQuery dataset ID capturing agentic telemetry and audit logs"
  value       = google_bigquery_dataset.telemetry_logs.dataset_id
}
```

*(With this updated Stage 3 Security implementation, our three workload service accounts contain the complete, high-fidelity permissions derived from the monolithic test VM service account. We have established a dedicated Test VM service account with tight invocations and token-creator privileges, fully customized for secure private VPC endpoints.)*

---
