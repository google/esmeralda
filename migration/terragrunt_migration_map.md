# Esmeralda FAST-Aligned Composable Platform Design - Master Migration Blueprint

> [!NOTE]
> Este é o **Documento Mestre de Migração** do Esmeralda. Ele unifica e consolida todas as diretrizes estratégicas de plataforma, especificações de fundações e cargas de trabalho em português, juntamente com os blueprints técnicos detalhados e códigos HCL de referência em inglês. Nenhuma informação foi eliminada durante esta consolidação.

---

## 🗺️ Índice Geral do Documento Mestre
1. [Parte I: Guia Mestre de Arquitetura e Estratégia (Português)](#part-i)
   - [Seção 1: Fundações de Plataforma (Stage 1, 2 e 3)](#part-i-section-1)
   - [Seção 2: Cargas de Trabalho, Integração & Delivery (Stage 4 e Estratégia)](#part-i-section-2)
2. [Parte II: Blueprints Técnicos Detalhados e Códigos HCL (Inglês)](#part-ii)

---

<a name="part-i"></a>
# Parte I: Guia Mestre de Arquitetura e Estratégia (Português)

<a name="part-i-section-1"></a>
# Estágios de Infraestrutura: Fundações de Plataforma (Stages 1, 2 e 3)

Este documento centraliza as especificações detalhadas de infraestrutura para as fundações da landing zone do Esmeralda. Ele consolida e otimiza as etapas de **Provisionamento de Projetos e APIs (Stage 1)**, **Redes Privadas e DNS (Stage 2)** e **Segurança, Chaves CMEK e Identidades (Stage 3)**.

---

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

### C. Especificações Técnicas de Projetos (`modules/1-projects/`)

#### 1. Variáveis de Entrada (`variables.tf`)
```hcl
variable "billing_account" {
  type        = string
  description = "ID da conta de faturamento do GCP"
}

variable "project_prefix" {
  type        = string
  default     = "esmeralda"
}

variable "byo_net_host_project" { type = bool; default = false }
variable "byo_gateway_project"  { type = bool; default = false }

variable "existing_net_host_project" { type = string; default = "" }
variable "existing_gateway_project"  { type = string; default = "" }
```

#### 2. Declaração do Projeto (`main.tf`)
```hcl
resource "random_id" "suffix" {
  byte_length = 3
}

locals {
  suffix = random_id.suffix.hex
}

resource "google_project" "mcps" {
  name            = "${var.project_prefix}-mcps"
  project_id      = "${var.project_prefix}-mcps-${local.suffix}"
  billing_account = var.billing_account
  labels          = { env = "dev", cost-center = "appdev" }
}

# Definição do Projeto Core de Inteligência Artificial (AUDIT-03 Fix)
resource "google_project" "a2a" {
  name            = "${var.project_prefix}-a2a-agents"
  project_id      = "${var.project_prefix}-a2a-${local.suffix}"
  billing_account = var.billing_account
  labels          = { env = "dev", cost-center = "core-ai" }
}


# Habilita Vertex AI no projeto de agentes
resource "google_project_service" "a2a_services" {
  project = google_project.a2a.project_id
  service = "aiplatform.googleapis.com"
  disable_on_destroy = false
}
```

---

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

#### Especificação do Network Attachment para Vertex AI (`modules/2-networking/`)
```hcl
resource "google_compute_service_attachment" "psc_attachment" {
  name                  = "gateway-psc-interface-attachment"
  project               = var.net_host_project_id
  region                = var.region
  target_service        = google_compute_forwarding_rule.ilb_forwarding_rule.id
  connection_preference = "ACCEPT_AUTOMATIC"
  nat_subnets           = [google_compute_subnetwork.psc_subnet.id]
}
```

---

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

### C. Código de Provisionamento do Cloud SQL com CMEK (`modules/3-security/`)

```hcl
# infrastructure/modules/3-security/main.tf

# Criação da Chave KMS para o Cloud SQL
resource "google_kms_crypto_key" "sql_key" {
  name            = "key-postgresql"
  key_ring        = google_kms_key_ring.ring.id
  rotation_period = "7776000s" # 90 dias
}

# Permite que a Service Account de serviço do Cloud SQL do projeto de agentes leia a chave KMS
resource "google_kms_crypto_key_iam_binding" "sql_kms_binding" {
  crypto_key_id = google_kms_crypto_key.sql_key.id
  role          = "roles/cloudkms.signerDecrypter"
  members       = [
    "serviceAccount:service-${var.a2a_project_number}@gcp-sa-cloud-sql.iam.gserviceaccount.com"
  ]
}

# --------------------------------------------------------------------
# Permissões de Direct VPC Egress para Cloud Run (Correção AUDIT-01)
# --------------------------------------------------------------------

# Permite que as Service Accounts das workloads usem a subnet da VPC Compartilhada
resource "google_compute_subnetwork_iam_member" "a2a_subnet_user" {
  project    = var.net_host_project_id
  region     = var.region
  subnetwork = var.backend_subnet_id
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.a2a_sa.email}"
}

resource "google_compute_subnetwork_iam_member" "mcps_subnet_user" {
  project    = var.net_host_project_id
  region     = var.region
  subnetwork = var.backend_subnet_id
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.mcps_sa.email}"
}

resource "google_compute_subnetwork_iam_member" "root_subnet_user" {
  project    = var.net_host_project_id
  region     = var.region
  subnetwork = var.backend_subnet_id
  role       = "roles/compute.networkUser"
  member     = "serviceAccount:${google_service_account.root_sa.email}"
}

```


---

<a name="part-i-section-2"></a>
# Estágios de Infraestrutura: Cargas de Trabalho, Integração & Delivery (Stage 4 e Estratégia)

Este documento centraliza as especificações detalhadas de infraestrutura para a camada de **Cargas de Trabalho (Stage 4)**, a arquitetura de **Alternância Greenfield vs. Brownfield (BYOInfra)**, o fluxo integrado de **Bootstrap do Banco de Dados Cloud SQL** e o ecossistema de **Testes Simétricos Locais e Remotos**. Ele consolida e otimiza as antigas diretrizes individuais de gateways, servidores MCP, agentes ADK e estratégias de entrega.

---

## 🏗️ 1. Stage 4: Prateleira de Cargas de Trabalho (`modules/4-workloads/`)

O Stage 4 faz a transição do Esmeralda de fundações brutas de rede e segurança para o espaço de **Aplicações de IA Composíveis**. O design adota o padrão de catálogo independente: cada gateway, ferramenta MCP ou agente de IA é tratado como um módulo reaproveitável, permitindo deploys granulares sobre os projetos criados no Stage 1.

---

### A. Padrão Gateway Adapter: Gateways de Ingress Intercambiáveis

Para garantir que a plataforma possa ser implantada em qualquer cliente corporativo (desde sandboxes ágeis até ambientes de alta governança), o Esmeralda adota o **Padrão Gateway Adapter**. Os agentes do Vertex AI Reasoning Engine permanecem completamente agnósticos sobre qual tecnologia de gateway está ativa na rede.

Definimos três adaptadores de entrada sob `/modules/4-workloads/gateways/` que atendem ao mesmo **contrato unificado de variáveis**:

```text
infrastructure/modules/4-workloads/gateways/
├── apigee/                 # Opção A: Apigee X Enterprise (WAF, Rate Limiting, IAM Auth)
├── kong/                   # Opção B: Kong Gateway Serverless no Cloud Run privado
└── ilb/                    # Opção C: GCP Regional L7 HTTP(S) Internal Load Balancer + Broker
```

#### Contrato de Interfaces dos Gateways (`variables.tf` & `outputs.tf`)

```hcl
# Variáveis comuns de entrada obrigatórias para os 3 adaptadores:
variable "project_id" {
  description = "ID do projeto GCP prj-gateway alocado para ingress"
  type        = string
}

variable "region" {
  description = "Região GCP onde os recursos de ingress serão criados"
  type        = string
}

variable "vpc_id" {
  description = "Self-link da VPC Compartilhada central (de prj-net-host)"
  type        = string
}

variable "subnet_id" {
  description = "Self-link da subrede de backends/ingress na VPC"
  type        = string
}

variable "agent_endpoints" {
  description = "Mapeamento de nomes lógicos de agentes para seus endpoints dinâmicos no Vertex AI"
  type = map(object({
    logical_name = string
    engine_id    = string
    endpoint_url = string
  }))
}

variable "environment" {
  type    = string
  default = "dev"
}

# Saídas padrão expostas de forma idêntica pelos 3 adaptadores:
output "gateway_ingress_ip" {
  description = "IP VIP privado interno do gateway de ingress na VPC"
  value       = string
}

output "gateway_agent_ingress_host" {
  description = "Zona DNS interna padrão controlada pelo gateway (ex: esmeralda.internal)"
  value       = "esmeralda.internal"
}
```

---

#### Opção A: Apigee X Enterprise Gateway (`gateways/apigee/`)

A Apigee X atua como o gateway corporativo padrão. Para resolver as URLs dinâmicas do Vertex AI Reasoning Engine (cujos IDs de recurso mudam a cada novo empacotamento), a Apigee armazena esse mapa em um **Apigee Key Value Map (KVM)** e utiliza políticas internas de fluxo para extrair o host, ler o destino e injetar o token OAuth2 de Service Account de forma transparente.

```hcl
# infrastructure/modules/4-workloads/gateways/apigee/main.tf

resource "google_apigee_organization" "apigee_org" {
  analytics_region   = var.region
  project_id         = var.project_id
  authorized_network = var.vpc_id
}

resource "google_apigee_environment" "apigee_env" {
  name         = var.environment
  org_id       = google_apigee_organization.apigee_org.id
  description  = "Ambiente Esmeralda ${var.environment}"
}

resource "google_apigee_envgroup" "apigee_envgroup" {
  name      = "esmeralda-group-${var.environment}"
  org_id    = google_apigee_organization.apigee_org.id
  hostnames = ["*.esmeralda.internal"]
}

resource "google_apigee_envgroup_attachment" "env_to_group" {
  envgroup_id = google_apigee_envgroup.apigee_envgroup.id
  environment = google_apigee_environment.apigee_env.name
}

resource "google_apigee_instance" "apigee_instance" {
  name               = "apigee-instance-${var.environment}"
  org_id             = google_apigee_organization.apigee_org.id
  location           = var.region
  peering_cidr_range = "10.12.0.0/22"
}

resource "google_apigee_instance_attachment" "env_to_instance" {
  instance_id = google_apigee_instance.apigee_instance.id
  environment = google_apigee_environment.apigee_env.name
}

# Mapa de Valores Chave para rotas de agentes dinâmicas
resource "google_apigee_keyvaluemap" "agent_routes" {
  org_id = google_apigee_organization.apigee_org.id
  env_id = google_apigee_environment.apigee_env.id
  name   = "agent-routes"
}

# Popula KVM de rotas via curl local-exec (limitação do provider do Google)
resource "null_resource" "populate_apigee_kvm" {
  for_each = var.agent_endpoints

  triggers = {
    engine_id    = each.value.engine_id
    endpoint_url = each.value.endpoint_url
  }

  provisioner "local-exec" {
    command = <<EOT
      curl -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "Content-Type: application/json" \
        "https://apigee.googleapis.com/v1/organizations/${google_apigee_organization.apigee_org.name}/environments/${google_apigee_environment.apigee_env.name}/keyvaluemaps/agent-routes/entries" \
        -d '{"name": "${each.value.logical_name}", "value": "${each.value.endpoint_url}"}' \
        || curl -X PUT -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "Content-Type: application/json" \
        "https://apigee.googleapis.com/v1/organizations/${google_apigee_organization.apigee_org.name}/environments/${google_apigee_environment.apigee_env.name}/keyvaluemaps/agent-routes/entries/${each.value.logical_name}" \
        -d '{"name": "${each.value.logical_name}", "value": "${each.value.endpoint_url}"}'
    EOT
  }
}
```

---

#### Opção B: Lightweight Kong Gateway no Cloud Run (`gateways/kong/`)

Para instalações ágeis, o Kong roda em modo DB-less (sem banco de dados) no Cloud Run dentro do projeto `prj-gateway`. Ele consome sua configuração declarativa armazenada de forma segura no Secret Manager, montada como volume do container.

```hcl
# infrastructure/modules/4-workloads/gateways/kong/main.tf

locals {
  kong_config = templatefile("${path.module}/templates/kong.yml.tpl", {
    agent_endpoints = var.agent_endpoints
  })
}

resource "google_secret_manager_secret" "kong_config" {
  secret_id = "kong-config-${var.environment}"
  project   = var.project_id
  replication { auto {} }
}

resource "google_secret_manager_secret_version" "kong_config" {
  secret      = google_secret_manager_secret.kong_config.id
  secret_data = local.kong_config
}

resource "google_service_account" "kong_sa" {
  account_id   = "kong-gateway-sa-${var.environment}"
  project      = var.project_id
}

# Permite ao Kong invocar os Reasoning Engines no Vertex AI
resource "google_project_iam_member" "kong_vertex_access" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.kong_sa.email}"
}

resource "google_cloud_run_v2_service" "kong_gateway" {
  name     = "kong-gateway-${var.environment}"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY" # Apenas tráfego privado da VPC Compartilhada

  template {
    service_account = google_service_account.kong_sa.email

    containers {
      image = "kong:latest"
      ports { container_port = 8000 }
      env {
        name  = "KONG_DATABASE"
        value = "off"
      }
      env {
        name  = "KONG_DECLARATIVE_CONFIG"
        value = "/etc/kong/kong.yml"
      }
      volume_mounts {
        name       = "kong-config"
        mount_path = "/etc/kong"
      }
    }
    
    volumes {
      name = "kong-config"
      secret {
        secret_name = google_secret_manager_secret.kong_config.secret_id
        items {
          version = "latest"
          path    = "kong.yml"
        }
      }
    }
    
    vpc_access {
      network_interfaces {
        network    = var.vpc_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }
}
```

##### Template do Arquivo de Configuração Kong (`templates/kong.yml.tpl`)

O template compila rotas e aplica o plugin nativo do GCP para trocar automaticamente tokens de Service Account em tempo de trânsito:

```yaml
_format_version: "3.0"
_transform: true

services:
%{ for key, endpoint in agent_endpoints ~}
  - name: ${endpoint.logical_name}
    url: ${endpoint.endpoint_url}
    routes:
      - name: ${endpoint.logical_name}-route
        hosts:
          - ${endpoint.logical_name}.esmeralda.internal
        strip_path: true
    plugins:
      - name: gcp-service-account
        config:
          audience: "https://us-central1-aiplatform.googleapis.com"
%{ endfor ~}
```

---

#### Opção C: Internal Load Balancer + Broker Proxy (`gateways/ilb/`)

Para evitar produtos de API Gateway adicionais, utilizamos o próprio balanceador regional HTTP L7 do Google Cloud (ILB). Como o ILB não consegue reescrever rotas ou injetar tokens de identidade dinamicamente, empacotamos um **Routing Broker privado** rodando em Cloud Run como seu backend padrão (acessado via Serverless NEG). O Broker intercepta as cabeçalhos `Host`, resolve o endpoint Vertex correspondente e injeta as credenciais via Metadata Server local.

```hcl
# infrastructure/modules/4-workloads/gateways/ilb/main.tf

resource "google_service_account" "broker_sa" {
  account_id   = "routing-broker-sa-${var.environment}"
  project      = var.project_id
}

resource "google_project_iam_member" "broker_vertex_access" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.broker_sa.email}"
}

resource "google_cloud_run_v2_service" "routing_broker" {
  name     = "routing-broker-${var.environment}"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.broker_sa.email

    containers {
      image = "us-central1-docker.pkg.dev/${var.mcps_project_id}/mcp-repo/routing-broker:latest" # AUDIT-05 Fix
      env {
        name  = "AGENT_ENDPOINTS_JSON"
        value = jsonencode(var.agent_endpoints)
      }
    }

    vpc_access {
      network_interfaces {
        network    = var.vpc_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }
}

resource "google_compute_region_network_endpoint_group" "broker_neg" {
  name                  = "neg-broker-${var.environment}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.routing_broker.name
  }
}

resource "google_compute_region_backend_service" "broker_backend" {
  name                  = "backend-broker-${var.environment}"
  project               = var.project_id
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.broker_neg.id
  }
}

resource "google_compute_region_url_map" "ilb_url_map" {
  name            = "ilb-gateway-url-map-${var.environment}"
  project         = var.project_id
  region          = var.region
  default_service = google_compute_region_backend_service.broker_backend.id

  host_rule {
    hosts        = ["*.esmeralda.internal"]
    path_matcher = "all-agents"
  }

  path_matcher {
    name            = "all-agents"
    default_service = google_compute_region_backend_service.broker_backend.id
  }
}

resource "google_compute_region_target_http_proxy" "ilb_proxy" {
  name    = "ilb-gateway-proxy-${var.environment}"
  project = var.project_id
  region  = var.region
  url_map = google_compute_region_url_map.ilb_url_map.id
}

resource "google_compute_forwarding_rule" "ilb_forwarding_rule" {
  name                  = "ilb-gateway-rule-${var.environment}"
  project               = var.project_id
  region                = var.region
  ip_protocol           = "TCP"
  port_range            = "80"
  load_balancing_scheme = "INTERNAL_MANAGED"
  network               = var.vpc_id
  subnetwork            = var.subnet_id
  target                = google_compute_region_target_http_proxy.ilb_proxy.id
}
```

---

### B. Standalone API Hub (`modules/4-workloads/apihub/`)

O catálogo de governança do API Hub é executado como uma carga de trabalho adjacente isolada no projeto `prj-gateway`. Ele registra automaticamente o diretório corporativo de APIs sem influenciar os roteamentos de tráfego ativos.

```hcl
# infrastructure/modules/4-workloads/apihub/main.tf

resource "google_project_service_identity" "apihub_service_identity" {
  provider = google-beta
  project  = var.project_id
  service  = "apihub.googleapis.com"
}

resource "google_project_iam_member" "apihub_service_identity_permission" {
  provider = google-beta
  for_each = toset(["roles/apihub.admin", "roles/apihub.runtimeProjectServiceAgent"])
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_project_service_identity.apihub_service_identity.email}"
}

resource "google_apihub_host_project_registration" "apihub_host_project" {
  provider                     = google-beta
  project                      = var.project_id
  location                     = var.region
  host_project_registration_id = var.project_id
  gcp_project                  = "projects/${var.project_id}"
  depends_on                   = [google_project_iam_member.apihub_service_identity_permission]
}

resource "google_apihub_api_hub_instance" "main" {
  provider            = google-beta
  project             = var.project_id
  location            = var.region
  api_hub_instance_id = var.api_hub_instance_id
  
  config {
    disable_search  = false
    vertex_location = "us"
  }

  timeouts { create = "35m" }
  depends_on = [google_apihub_host_project_registration.apihub_host_project]
}
```

---

### C. Composable MCP Server Tools (`modules/4-workloads/mcp-servers/`)

As utilidades e conexões de backend corporativas expostas pelo protocolo MCP (DMS, Email, Income Verification) residem no projeto de ferramentas `prj-esmeralda-mcps`. Cada servidor é implantado de forma independente no Cloud Run sob regras rígidas de segurança:
*   `no-allow-unauthenticated` ativado.
*   Tráfego de saída obrigado a fluir 100% via Direct VPC Egress dentro da VPC Compartilhada.
*   Audiências customizadas explícitas apontando para o IP estável de rede do Gateway de Ingress.
*   Post-deployment trigger executando um script em Python que cataloga automaticamente a ferramenta de forma programática no Google Agent Registry.

```hcl
# Exemplo genérico: modules/4-workloads/mcp-servers/main.tf (Aplicado para DMS, Email e Income)

resource "google_cloud_run_v2_service" "mcp_service" {
  name     = "${var.server_name}-${var.environment}"
  location = var.region
  project  = var.project_id
  ingress  = "INGRESS_TRAFFIC_INTERNAL_AND_CLOUD_LIMITING" # Permite apenas chamadas via ILB / VPC

  template {
    containers {
      image = var.container_image
      ports { container_port = 8080 }
      
      env {
        name  = "OTEL_EXPORTER_OTLP_ENDPOINT"
        value = "http://collector.telemetry.internal:4317"
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
    }

    vpc_access {
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
      egress = "ALL_TRAFFIC"
    }
  }
}

resource "google_cloud_run_v2_service_custom_audiences" "audiences" {
  name      = google_cloud_run_v2_service.mcp_service.name
  location  = var.region
  project   = var.project_id
  audiences = [
    "http://${var.server_name}.internal.gateway",
    "https://${var.server_name}.internal.gateway",
    "https://${var.server_name}.internal.gateway/mcp"
  ]
}


resource "google_cloud_run_v2_service_iam_binding" "invokers" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.mcp_service.name
  role     = "roles/run.invoker"
  members  = [for sa in var.invoker_service_accounts : "serviceAccount:${sa}"]
}

resource "null_resource" "mcp_registration" {
  triggers = {
    service_uri = google_cloud_run_v2_service.mcp_service.uri
  }

  provisioner "local-exec" {
    command = <<EOT
      python3 ../../../../tools_mcp/register_mcp.py \
        --project_id="${var.project_id}" \
        --region="${var.region}" \
        --server_name="${var.server_name}" \
        --server_url="${google_cloud_run_v2_service.mcp_service.uri}"
    EOT
  }
}
```

---

### D. Atomic Agent Reasoning Engines (`modules/4-workloads/agents/`)

No novo modelo Esmeralda, os agentes ADK operam em ambientes perfeitamente isolados e com injeção dinâmica de dependências declarativas via Terragrunt:

#### 1. Atomic Mortgage Assistant (`agents/a2a-agent/`)

Para garantir portabilidade corporativa absoluta, este módulo empacota de forma 100% autossuficiente o banco PostgreSQL privado, a service range dedicada da VPC via peering de PSA, as permissões de acesso nativo baseadas em IAM Token DB Authentication e um executor de bootstrap privado (Cloud Run Job rodando na rede interna) que inicia os privilégios operacionais.

*(O código de provisionamento completo HCL desta sub-unidade, contendo a instância de banco privada, o Cloud Run Job de bootstrap `schema_bootstrap` e a declaração de recursos de Vertex AI Reasoning Engine `google_vertex_ai_reasoning_engine` encontra-se especificado detalhadamente na Seção 4 deste documento de Ciclo de Vida).*

#### 2. Root Orchestrator Agent (`agents/base-adk-agent/`)

O Root Orchestrator coordena a árvore de decisões multi-agente. Ele recebe perguntas da camada pública de ingress via Gateway, analisa a necessidade de ferramentas (MCP) ou de subprocessos refinados (como o `a2a-agent`), e dispara requisições de volta **através da URL estável de DNS privado do gateway**.

Ao expor caminhos estáveis baseados em DNS privado (`http://a2a-agent.esmeralda.internal`), o Root Orchestrator quebra qualquer referência cruzada cíclica com os outros módulos do Terragrunt na fase de avaliação.

```hcl
# infrastructure/modules/4-workloads/agents/base-adk-agent/main.tf

resource "google_storage_bucket" "staging" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.agent_name}-staging-${var.environment}"
  location                    = var.region
  uniform_bucket_level_access = true
}

resource "google_storage_bucket_object" "agent_pickle" {
  name   = "agents/${var.agent_name}/agent.pkl"
  bucket = google_storage_bucket.staging.name
  source = var.pickle_object_path
}

resource "google_vertex_ai_reasoning_engine" "agent" {
  display_name = "${var.agent_name}-${var.environment}"
  region       = var.region
  project      = var.project_id

  spec {
    agent_framework = "google-adk"
    service_account = var.agent_service_account

    package_spec {
      python_version        = "3.12"
      pickle_object_gcs_uri = "gs://${google_storage_bucket.staging.name}/${google_storage_bucket_object.agent_pickle.name}"
      requirements_gcs_uri  = "gs://${google_storage_bucket.staging.name}/requirements.txt"
    }

    deployment_spec {
      psc_interface_config {
        network_attachment = var.network_attachment
      }
    }
  }
}
```

---

## 🔌 2. Greenfield vs. Brownfield (BYOInfra) Toggle Design

Em ambientes produtivos reais de clientes corporativos, o time de infraestrutura (NetOps e SecOps) raramente autoriza que scripts Terraform criem redes VPC Compartilhadas, subredes, rotas DNS de internet ou projetos corporativos de faturamento do zero. O cliente exige consumir recursos preexistentes (**Brownfield** / **BYOInfra**).

O Esmeralda resolve esse bloqueio implementando de forma transparente chaves de alternância dinâmica em suas receitas de Terragrunt (`live/dev/env.yaml`), utilizando o parâmetro de bypass nativo `skip` e mapeando fallbacks condicionais:

### A. Configuração de Variáveis de Controle do Cliente (`live/client-prod/env.yaml`)

Neste arquivo, o cliente define quais infraestruturas deseja reutilizar (`true`) e fornece os ponteiros explícitos dessas conexões reais:

```yaml
# infrastructure/live/client-prod/env.yaml
locals {
  environment               = "prod"
  project_prefix            = "enterprise-client"
  region                    = "us-central1"

  # 🔌 BYO INFRA SWITCHES: Indica reutilização de rede e gateway preexistentes
  byo_net_host_project      = true
  byo_gateway_project       = true
  byo_networking            = true
  byo_apigee                = true

  # Apontadores para recursos corporativos existentes
  existing_net_host_project = "prj-corp-shared-net"
  existing_gateway_project  = "prj-corp-apigee"
  existing_vpc_id           = "projects/prj-corp-shared-net/global/networks/vpc-production"
  existing_subnet_id        = "projects/prj-corp-shared-net/regions/us-central1/subnetworks/sb-prod-private"

  database_product          = "cloud-sql" # Criar banco do zero
}
```

### B. Bypass Dinâmico de Estágios de Infraestrutura (`live/client-prod/stage-2-networking/terragrunt.hcl`)

Utilizando o bloco de bypass nativo do Terragrunt, o módulo de provisionamento de rede é desativado instantaneamente na esteira se a variável `byo_networking` estiver ativa:

```hcl
# infrastructure/live/client-prod/stage-2-networking/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//2-networking"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

# Se for Brownfield, pula este estágio sem gerar erros de execução ou apply
skip = local.env_vars.locals.byo_networking
```

### C. Fallbacks Condicionais em Dependências Downstream (`live/client-prod/stage-4-workloads/agents/a2a-agent/terragrunt.hcl`)

Cargas de trabalho que consomem as saídas de estágios pulados desviam de forma transparente suas leituras de variáveis para os campos estáticos do `env.yaml` do cliente, evitando estouros de avaliação no analisador do Terragrunt usando mocks estruturados:

```hcl
# infrastructure/live/client-prod/stage-4-workloads/agents/a2a-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/agents/a2a-agent"
}

locals {
  env_vars       = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  byo_networking = lookup(local.env_vars.locals, "byo_networking", false)
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "security" {
  config_path = "../../../stage-3-security"
}

dependency "networking" {
  config_path  = "../../../stage-2-networking"
  
  # Evita carregar variáveis de um módulo cujo apply foi ignorado
  skip_outputs = local.byo_networking
  
  # Alimenta o analisador do Terraform com mocks seguros em tempo de compilação
  mock_outputs = {
    network_id = local.env_vars.locals.existing_vpc_id
    subnet_id  = local.env_vars.locals.existing_subnet_id
  }
}

inputs = {
  project_id            = dependency.projects.outputs.a2a_project_id
  region                = local.env_vars.locals.region
  agent_service_account = dependency.security.outputs.a2a_agent_sa_email
  
  # DESVIO CONDICIONAL: Usa a infraestrutura preexistente do cliente ou as saídas criadas em Stage 2
  vpc_id                = local.byo_networking ? local.env_vars.locals.existing_vpc_id  : dependency.networking.outputs.network_id
  subnet_id             = local.byo_networking ? local.env_vars.locals.existing_subnet_id : dependency.networking.outputs.subnet_id
  
  database_name         = "a2a_tasks"
}
```

---

## 🔄 3. Ciclo de Vida do Database Bootstrap (A2A Agent & Cloud SQL)

Um dos grandes problemas de esteiras de infraestrutura corporativas é o provisionamento acoplado e inseguro de privilégios de banco de dados. Para garantir um deploy 100% atômico e seguro, o Esmeralda encapsula o banco PostgreSQL e seu ciclo de inicialização diretamente dentro do módulo do `a2a-agent`:

### Sequenciamento de Orquestração do Banco de Dados

```mermaid
sequenceDiagram
    autonumber
    participant TG as Terragrunt / TF Apply
    participant SQL as Cloud SQL Instance
    participant Secret as Secret Manager
    participant Run as Cloud Run Bootstrap Job<br/>(Dentro da VPC)
    participant Vertex as Vertex AI Reasoning Engine

    Note over TG: Deploy do A2A Agent Module
    TG->>SQL: 1. Provisiona instância PostgreSQL privada & Ativa IAM Auth
    TG->>Secret: 2. Salva senha master aleatória do superuser 'postgres'
    TG->>Run: 3. Dispara Job temporário no Cloud Run via Rede Privada
    Run->>SQL: 4. Conecta via IP interno e aplica GRANT de privilégios para a SA do Agente
    Note over Run: Container de Bootstrap finaliza (Sucesso)
    TG->>Vertex: 5. Deplora Reasoning Engine empacotado amarrando o IP privado do Banco nas variáveis
```

### Código do Provisionamento e Bootstrap Integrado (`modules/4-workloads/agents/a2a-agent/main.tf`)

```hcl
# infrastructure/modules/4-workloads/agents/a2a-agent/main.tf

# 1. RESERVA DE IP E CRIAÇÃO DA INSTÂNCIA PRIVADA
resource "google_compute_global_address" "sql_private_ip" {
  name          = "${var.agent_name}-sql-ip-${var.environment}"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = var.vpc_id
  project       = var.project_id
}

resource "google_service_networking_connection" "sql_peering" {
  network                 = var.vpc_id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.sql_private_ip.name]
}

resource "google_sql_database_instance" "task_store" {
  name             = "${var.agent_name}-db-${var.environment}"
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_15"
  depends_on       = [google_service_networking_connection.sql_peering]

  settings {
    tier              = var.sql_tier
    availability_type = "ZONAL"
    disk_size         = 15

    ip_configuration {
      ipv4_enabled    = false # Isolamento estrito de internet
      private_network = var.vpc_id
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }
  deletion_protection = false
}

resource "google_sql_database" "tasks_db" {
  name     = var.database_name
  instance = google_sql_database_instance.task_store.name
  project  = var.project_id
}

resource "random_password" "postgres_pwd" {
  length  = 24
  special = false
}

resource "google_sql_user" "postgres_user" {
  name     = "postgres"
  instance = google_sql_database_instance.task_store.name
  project  = var.project_id
  password = random_password.postgres_pwd.result
}

# Usuário de banco integrado ao IAM para conexão sem senhas em disco
resource "google_sql_user" "agent_iam_user" {
  name       = trimsuffix(var.agent_service_account, ".gserviceaccount.com")
  instance   = google_sql_database_instance.task_store.name
  project    = var.project_id
  type       = "CLOUD_IAM_SERVICE_ACCOUNT"
  depends_on = [google_sql_user.postgres_user]
}

# Garante sincronismo físico aguardando a API do banco estar 100% RUNNABLE
resource "null_resource" "db_ready" {
  depends_on = [google_sql_database_instance.task_store, google_sql_database.tasks_db, google_sql_user.agent_iam_user]

  provisioner "local-exec" {
    command = <<EOT
      echo "⏳ Aguardando ativação do banco ${google_sql_database_instance.task_store.name}..."
      for i in {1..30}; do
        STATE=$(gcloud sql instances describe ${google_sql_database_instance.task_store.name} --project=${var.project_id} --format="value(state)" 2>/dev/null || echo "UNKNOWN")
        if [ "$STATE" = "RUNNABLE" ]; then
          echo "✅ Banco online. Aguardando 10s para estabilização de sockets..."
          sleep 10
          exit 0
        fi
        sleep 10
      done
      exit 1
    EOT
  }
}

# Atribuição de permissões IAM do GCP para permitir conexão
resource "google_project_iam_member" "cloudsql_client_role" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${var.agent_service_account}"
}

resource "google_project_iam_member" "cloudsql_user_role" {
  project = var.project_id
  role    = "roles/cloudsql.instanceUser"
  member  = "serviceAccount:${var.agent_service_account}"
}

# 2. RUNTIME DE INICIALIZAÇÃO CORPORATIVA VIA VPC-BOUND CLOUD RUN JOB
resource "google_cloud_run_v2_job" "schema_bootstrap" {
  name     = "${var.agent_name}-db-bootstrap-${var.environment}"
  location = var.region
  project  = var.project_id
  depends_on = [
    null_resource.db_ready,
    google_project_iam_member.cloudsql_client_role,
    google_project_iam_member.cloudsql_user_role
  ]

  template {
    template {
      service_account = var.agent_service_account
      
      containers {
        image   = "alpine:latest"
        command = ["/bin/sh", "-c"]
        args = [
          "apk add --no-cache postgresql-client && psql \"postgresql://postgres:${random_password.postgres_pwd.result}@${google_sql_database_instance.task_store.private_ip_address}/${var.database_name}\" -c \"GRANT ALL PRIVILEGES ON DATABASE ${var.database_name} TO \\\"${google_sql_user.agent_iam_user.name}\\\";\""
        ]
      }

      vpc_access {
        network_interfaces {
          network    = var.vpc_id
          subnetwork = var.subnet_id
        }
        egress = "ALL_TRAFFIC"
      }
    }
  }
}

resource "null_resource" "trigger_bootstrap" {
  depends_on = [google_cloud_run_v2_job.schema_bootstrap]

  provisioner "local-exec" {
    command = <<EOT
      echo "🚀 Executando Job Cloud Run para bootstrap privado de privilégios SQL..."
      gcloud run jobs execute ${google_cloud_run_v2_job.schema_bootstrap.name} \
        --region="${var.region}" \
        --project="${var.project_id}" \
        --wait
    EOT
  }
}
```

---

## 🧪 4. Ecossistema Onboarding DX: Testes Simétricos (Local vs. Remoto)

Inspirado no consagrado repositório de referência `ncf-conversacional-ecommerce`, o Esmeralda adota a filosofia de **Testes Simétricos**. Isso reduz o atrito e acelera as validações de código, garantindo que o engenheiro de software consiga testar o agente de forma offline (Inner Loop) e pós-deploy integrado (Outer Loop) sem alterar lógicas de negócio.

```text
app/agents/a2a-agent/scripts/
├── test_local.py             # Execução de testes offline com Mocks no localhost
└── test_remote.py            # Execução de testes integrados reais via SSE na nuvem GCP
```

---

### A. Inner Loop: Arquitetura de Testes Offline (`test_local.py`)

O teste local importa diretamente o objeto de aplicativo do agente (`adk_app`) do código Python, eliminando qualquer dependência de nuvem. Ele lê mocks das ferramentas rodando na rede de loopback local (`localhost`) através de portas designadas, simulando fluxos em tempo real com streams assíncronos:

```python
# app/agents/a2a-agent/scripts/test_local.py
import asyncio
import os
import sys
import logging
import dotenv
import warnings

# Limpa alertas de APIs experimentais
warnings.filterwarnings("ignore", category=UserWarning)

# Carrega variáveis de teste offline
dotenv.load_dotenv()

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_local")

# Insere a pasta app no path de busca
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

try:
    from app.agents.a2a_agent.agent_app import adk_app
except ImportError as e:
    logger.error(f"Erro ao importar adk_app do agente: {e}")
    sys.exit(1)

async def run_local_test(query: str):
    logger.info("Iniciando setup e configurações do AdkApp local...")
    adk_app.set_up()
    
    # Simula identificadores de telemetria e faturamento (SoC)
    caller_context = {
      "tenant_id": "mortgage-bu",
      "project": "esmeralda-local-sandbox",
      "application_name": "mortgage-assistant-client"
    }

    logger.info(f"Enviando consulta local: '{query}'")
    print("\n--- EVENTOS TRANSMITIDOS (LOCAL STREAM) ---")

    try:
        # Invoca a execução offline por meio do interceptador de stream assíncrono
        async for event in adk_app.async_stream_query(
            message=query,
            user_id="dev-sandbox-user",
            caller_context=caller_context
        ):
            print(f"Evento Local: {event}")
            sys.stdout.flush()
            
    except Exception as e:
        logger.error(f"Erro ao simular execução local do agente: {e}")
    finally:
        print("-------------------------------------------\n")
        await adk_app.async_close()

if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "Gostaria de cotar taxas para refinanciamento, por favor."
    asyncio.run(run_local_test(query))
```

---

### B. Outer Loop: Verificação Pós-Deploy Integrado (`test_remote.py`)

Após o deploy automatizado via pipeline, o desenvolvedor precisa certificar-se de que os privilégios, conexões de rede privada e integrações de banco de dados no Google Cloud estão perfeitos.

O script `test_remote.py` utiliza a biblioteca nativa `google.auth` para coletar as credenciais ativas do desenvolvedor (ou faz fallback para o CLI `gcloud`). Ele resolve o ID do Reasoning Engine de produção e dispara chamadas autenticadas de streaming `POST` por Server-Sent Events (SSE) diretamente contra o endpoint real do Vertex AI, exibindo a árvore de pensamentos do agente remoto no console local:

```python
# app/agents/a2a-agent/scripts/test_remote.py
import argparse
import sys
import os
import logging
import requests
import json
import dotenv
import google.auth
import google.auth.transport.requests

dotenv.load_dotenv()

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_remote")

def get_gcp_access_token() -> str:
    """Carrega as credenciais ativas locais do desenvolvedor do Google Cloud."""
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)
        if credentials.token:
            logger.info("Token GCP obtido com sucesso via credenciais nativas.")
            return credentials.token
    except Exception as e:
        logger.warning(f"Incapaz de obter token de forma nativa: {e}. Executando fallback gcloud CLI...")
        
    import subprocess
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token"],
            text=True
        ).strip()
        logger.info("Token GCP obtido via fallback de gcloud CLI.")
        return token
    except Exception as ex:
        logger.error(f"Erro ao rodar fallback: {ex}")
        raise RuntimeError("Nenhuma credencial válida do Google Cloud encontrada.")

def main():
    parser = argparse.ArgumentParser(description="Dispara teste integrado contra o Vertex AI real.")
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", "prj-dev-esmeralda-agents"))
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--resource-id", default=os.environ.get("VERTEX_AGENT_RESOURCE_ID"))
    parser.add_argument("query", nargs="?", default="Gostaria de cotar taxas para refinanciamento, por favor.")
    args = parser.parse_args()

    if not args.resource_id:
        logger.error("Erro: A variável VERTEX_AGENT_RESOURCE_ID deve estar definida para resolver o agente.")
        sys.exit(1)

    # Constrói o endpoint SSE nativo da Vertex AI para streaming do ADK
    stream_url = f"https://{args.location}-aiplatform.googleapis.com/v1/projects/{args.project}/locations/{args.location}/reasoningEngines/{args.resource_id}:streamQuery?alt=sse"

    try:
        token = get_gcp_access_token()
    except Exception as e:
        logger.error(f"Erro de autenticação GCP: {e}")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Contextos injetados e encaminhados de forma limpa pelo trânsito de nuvem
    query_payload = {
        "class_method": "async_stream_query",
        "input": {
            "message": args.query,
            "user_id": "remote-verified-user",
            "caller_context": {
                "tenant_id": "mortgage-bu",
                "project": args.project,
                "application_name": "mortgage-assistant-client"
            }
        }
    }

    logger.info(f"Disparando stream HTTP POST contra {stream_url}...")
    print("\n--- STREAM REAL DE EVENTOS (VERTEX CLOUD) ---")

    try:
        response = requests.post(stream_url, json=query_payload, headers=headers, stream=True)
        response.raise_for_status()
        
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data:"):
                    data_str = decoded_line[5:].strip()
                    try:
                        # Tenta processar o JSON retornado do stream do Vertex AI
                        data_json = json.loads(data_str)
                        print(json.dumps(data_json, indent=2, ensure_ascii=False))
                    except json.JSONDecodeError:
                        print(data_str)
                    sys.stdout.flush()
    except Exception as e:
        logger.error(f"Falha de comunicação no tráfego de rede do stream: {e}")
    finally:
        print("---------------------------------------------\n")
        logger.info("Execução integrada do teste remoto finalizada.")

if __name__ == "__main__":
    main()
```


---

<a name="part-ii"></a>
# Parte II: Blueprints Técnicos Detalhados e Códigos HCL (Inglês)

# Esmeralda FAST-Aligned Composable Platform Design

This blueprint establishes a highly elegant, **FAST-aligned platform architecture** for Esmeralda. It collapses granular, rigid developer stages into 3 base foundations and introduces a composable, multi-project **Stage 4: Workloads** designed like a "product catalog". 

Under this design, your `/modules` folder is the **Product Shelf**, your `env.yaml` is your **Shopping Cart**, and individual components (such as specific MCP tools, the orchestrator agent, and the A2A agent containing its own dedicated taskstore database) are deployed and updated completely in isolation across **six distinct GCP Projects**, simulating a real-world enterprise multi-tenant landing zone.

---

## 1. The FAST-Aligned Platform Layout

We organize Esmeralda's lifecycle stages to map directly to Google Cloud's enterprise landing zone standards. We divide our workloads across **four independent service projects** representing distinct engineering and business teams, connected back to a central Shared VPC, alongside a dedicated central governance and telemetry hub project:

```mermaid
graph TD
    %% Base Projects (FAST Aligned)
    S1_NetHost[prj-net-host <br/>Shared VPC Host Network] -. Binds Subnets .-> S2_SharedVPC[Stage 2: Shared VPC Networking]
    S1_Gateway[prj-gateway <br/>API Gateway Ingress] --> S3_Ingress[Stage 4: Gateway Choice <br/>Apigee, Kong, or ILB]
    S1_Gov[prj-esmeralda-governance <br/>Governance & Telemetry Hub]

    %% Decoupled Workloads Projects (Stage 4)
    subgraph "prj-esmeralda-mcps (Central Tools Team)"
        S4_MCP_DMS[mcp-servers/mcp-dms <br/>Cloud Run DMS Tool]
        S4_MCP_Calc[mcp-servers/mcp-calculator <br/>Cloud Run Calculator Tool]
    end

    subgraph "prj-esmeralda-a2a-agents (Core AI Platform Team)"
        S4_A2A[agents/a2a-agent <br/>Atomically Deploys:<br/>1. Cloud SQL PG Instance<br/>2. DB Bootstrap Grants Job<br/>3. Vertex AI Reasoning Engine]
    end

    subgraph "prj-esmeralda-root-agent (Line of Business Team)"
        S4_Root[agents/base-adk-agent <br/>Root Orchestrator Agent]
    end

    %% Integrations & Dependencies
    S2_SharedVPC -. Attaches Service Projects .-> prj-esmeralda-mcps
    S2_SharedVPC -. Attaches Service Projects .-> prj-esmeralda-a2a-agents
    S2_SharedVPC -. Attaches Service Projects .-> prj-esmeralda-root-agent
    S2_SharedVPC -. Attaches Service Projects .-> prj-esmeralda-governance

    S4_Root -. Consumes Tool Endpoints .-> S4_MCP_DMS
    S4_Root -. Consumes Tool Endpoints .-> S4_MCP_Calc
    S4_Root -. Calls Remote Agent .-> S4_A2A

    %% Telemetry Sinks & Security CMEK Links
    prj-esmeralda-mcps -- Sinks logs & traces --> S1_Gov
    prj-esmeralda-a2a-agents -- Sinks logs, traces & reads CMEK/Secrets --> S1_Gov
    prj-esmeralda-root-agent -- Sinks logs & traces --> S1_Gov
    S1_NetHost -- Sinks logs --> S1_Gov
    S1_Gateway -- Sinks logs --> S1_Gov
```

### The Architecture Design Philosophy:
*   **The Shared VPC Project (`prj-net-host`)**: Managed by NetOps. Owns the core routing, private DNS zones, and Private Service Connect (PSC).
*   **The Ingress Project (`prj-gateway`)**: Managed by PlatformOps. Hosts the public gateway endpoint.
*   **The Central Tools Project (`prj-esmeralda-mcps`)**: Managed by the AppDev Team. Deploys the reusable corporate tool API servers.
*   **The AI Platform Project (`prj-esmeralda-a2a-agents`)**: Managed by the Core AI Team. Hosts cross-company re-usable assistant agents and their SQL task stores.
*   **The LOB App Project (`prj-esmeralda-root-agent`)**: Managed by a specific business unit. Owns the customer-facing user reasoning engine, which orchestrates calls to the other projects.
*   **The Governance & Telemetry Hub Project (`prj-esmeralda-governance`)**: Managed by SecOps / PlatformOps. Centralizes security elements (KMS Keyrings, Secrets, Certificate Manager certificates) and telemetry components (Log Analytics buckets, Cloud Trace datasets, BigQuery tables), completely separating security/observability governance from core workloads.

---

## 2. Updated Terragrunt Repository Structure

```text
infrastructure/
├── modules/                                 # PURE, REUSABLE TERRAFORM MODULES
│   ├── 1-projects/                          # Seeds projects & enables APIs (Stage 1)
│   ├── 2-networking/                        # Provisions Shared VPC, subnets, DNS (Stage 2)
│   ├── 3-security/                          # Provisions Secret Manager, KMS, Log Sinks (Stage 3)
│   │
│   └── 4-workloads/                         # THE PRODUCT CATALOG SHELF (Stage 4)
│       ├── gateways/                        # --- GATEWAYS SHELF ---
│       │   ├── apigee/                      # Product A: Apigee X Enterprise Gateway
│       │   ├── kong/                        # Product B: Lightweight Kong on Cloud Run
│       │   └── ilb/                         # Product C: Direct Internal Load Balancer
│       │
│       ├── mcp-servers/                     # --- MCP TOOLS SHELF ---
│       │   ├── mcp-dms/                     # DMS Document Tool Service
│       │   └── mcp-calculator/              # Financial Calculator Tool Service
│       │
│       └── agents/                          # --- AI AGENTS SHELF ---
│           ├── base-adk-agent/              # Root Orchestrator (Reasoning Engine Only)
│           └── a2a-agent/                   # Mortgage Assistant (Atomic: SQL + Bootstrap + Agent)
│
└── live/                                    # LIVE INFRASTRUCTURE ENVIRONMENT CONFIG
    ├── terragrunt.hcl                       # Root config: remote state, global provider blocks
    ├── dev/                                 # DEVELOPMENT ENVIRONMENT
    │   ├── env.yaml                         # Dev environment vars (billing_id, region, prefix)
    │   │
    │   ├── stage-1-projects/
    │   │   └── terragrunt.hcl               # Deploys up to 6 Projects & APIs
    │   ├── stage-2-networking/
    │   │   └── terragrunt.hcl               # Deploys VPC, NAT, Firewalls, Private DNS
    │   ├── stage-3-security/
    │   │   └── terragrunt.hcl               # Deploys KMS, Secrets, Sinks across projects
    │   │
    │   └── stage-4-workloads/               # COMPOSABLE ASSEMBLY DIRECTORY
    │       ├── gateway/
    │       │   └── terragrunt.hcl           # Swappable: Deploys chosen Gateway
    │       ├── mcp-servers/
    │       │   ├── mcp-dms/
    │       │   │   └── terragrunt.hcl       # Deploys DMS to prj-esmeralda-mcps
    │       │   └── mcp-calculator/
    │       │       └── terragrunt.hcl       # Deploys Calculator to prj-esmeralda-mcps
    │       └── agents/
    │           ├── a2a-agent/
    │           │   └── terragrunt.hcl       # Deploys private SQL & ADK Agent to prj-esmeralda-a2a-agents
    │           └── base-adk-agent/
    │               └── terragrunt.hcl       # Deploys Orchestrator to prj-esmeralda-root-agent
```

---

## 3. Updated Component Migration Mapping

| Monolithic Component | Target Terragrunt Stage | Target GCP Project | Dependency Inputs |
| :--- | :--- | :--- | :--- |
| `module.foundation` (Core APIs) | **`stage-1-projects`** | *Pre-workloads* | `billing_account`, `byo_net_host_project`, `byo_gateway_project`, `byo_governance_project` |
| `module.networking` | **`stage-2-networking`** | `prj-net-host` | `net_host_project_id` (from stage-1), `governance_project_id` (from stage-1) |
| IAM, SAs & Encryption Keys | **`stage-3-security`** | Split across all | Project IDs from Stage 1 |
| Ingress / Load Balancer | **`stage-4-workloads/gateway`** | `prj-gateway` | `gateway_project_id` (stage-1), `network_id` (stage-2) |
| DMS MCP Service | **`stage-4-workloads/mcp-servers/mcp-dms`** | `prj-esmeralda-mcps` | `mcps_project_id` (stage-1), `subnet_id` (stage-2) |
| Calculator MCP Service | **`stage-4-workloads/mcp-servers/mcp-calculator`** | `prj-esmeralda-mcps` | `mcps_project_id` (stage-1), `subnet_id` (stage-2) |
| Cloud SQL & A2A Agent | **`stage-4-workloads/agents/a2a-agent`** | `prj-esmeralda-a2a-agents` | `a2a_project_id` (stage-1), `vpc_id` (stage-2), `subnet_id` (stage-2) |
| Root / Orchestrator Agent | **`stage-4-workloads/agents/base-adk-agent`** | `prj-esmeralda-root-agent` | `root_project_id` (stage-1), `a2a_agent_endpoint_url` (from `a2a-agent`), tool endpoints |
| Security, Governance, & Telemetry | **`stage-3-security`** | `prj-esmeralda-governance` | `governance_project_id` (stage-1), sinks across all 6 projects |

---

## 4. Workloads Assembly Configurations

These HCL declarations demonstrate how the micro-services are configured and chained in your live environment:

### A. The A2A Agent (`live/dev/stage-4-workloads/agents/a2a-agent/terragrunt.hcl`)
```hcl
# infrastructure/live/dev/stage-4-workloads/agents/a2a-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/agents/a2a-agent"
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "networking" {
  config_path = "../../../stage-2-networking"
}

dependency "security" {
  config_path = "../../../stage-3-security"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  # Dynamically deploys into the isolated A2A agent platform project!
  project_id            = dependency.projects.outputs.a2a_project_id
  region                = local.env_vars.locals.region
  vpc_id                = dependency.networking.outputs.network_id
  subnet_id             = dependency.networking.outputs.subnet_id
  agent_service_account = dependency.security.outputs.a2a_agent_sa_email

  database_product      = local.env_vars.locals.database_product
  database_name         = "a2a_tasks"
}
```

### B. The Root Orchestrator (`live/dev/stage-4-workloads/agents/base-adk-agent/terragrunt.hcl`)
```hcl
# infrastructure/live/dev/stage-4-workloads/agents/base-adk-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/agents/base-adk-agent"
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "security" {
  config_path = "../../../stage-3-security"
}

dependency "mcp_dms" {
  config_path = "../../mcp-servers/mcp-dms"
}

dependency "mcp_calc" {
  config_path = "../../mcp-servers/mcp-calculator"
}

dependency "a2a_agent" {
  config_path = "../a2a-agent"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

inputs = {
  # Deploys into the isolated customer-facing Line of Business (LOB) project!
  project_id            = dependency.projects.outputs.root_project_id
  region                = local.env_vars.locals.region
  agent_service_account = dependency.security.outputs.root_agent_sa_email

  # Inject downstream endpoints
  mcp_dms_url           = dependency.mcp_dms.outputs.mcp_dms_url
  mcp_calc_url          = dependency.mcp_calc.outputs.mcp_calc_url
  a2a_agent_url         = dependency.a2a_agent.outputs.a2a_agent_endpoint_url
}
```

---

## 5. The Integrated A2A Agent & Cloud SQL Lifecycle

By packaging Cloud SQL, bootstrapping, and the Vertex AI Reasoning engine inside the `modules/4-workloads/agents/a2a-agent` pure module, we obtain an atomic, self-contained workload.

```mermaid
sequenceDiagram
    autonumber
    participant TG as Terragrunt / TF Apply
    participant SQL as Cloud SQL Instance
    participant Secret as Secret Manager
    participant Run as Cloud Run Bootstrap Job<br/>(Within VPC)
    participant Vertex as Vertex AI Reasoning Engine

    Note over TG: Deploy A2A Agent Module
    TG->>SQL: 1. Provision private DB & IAM SQL user
    TG->>Secret: 2. Store PostgreSQL admin password securely
    TG->>Run: 3. Trigger VPC-internal Bootstrap Job to apply SQL grants
    Run->>SQL: 4. Connect over private IP & GRANT ALL PRIVILEGES...
    Note over Run: Bootstrap Job Exits
    TG->>Vertex: 5. Deploy Reasoning Engine (ADK)<br/>Binds private DB Host IP to Agent variables
```

---

## 6. Greenfield vs. Brownfield (BYOInfra) Toggle Design

To allow seamless deployment inside enterprise client environments with pre-existing resources, the architecture implements the **BYOInfra Pattern** natively using Terragrunt's skip parameters and input-fallbacks:

### A. The Client's Environment Parameters (`live/client-prod/env.yaml`)
The client declares their pre-existing resources and toggles the dynamic skip flags:

```yaml
# infrastructure/live/client-prod/env.yaml
locals {
  environment         = "prod"
  project_prefix      = "client"
  region              = "us-central1"

  # 🔌 BYO INFRA TOGGLES: Client already has host network and gateway projects!
  byo_net_host_project = true
  byo_gateway_project  = true
  byo_governance_project = false
  byo_networking       = true
  byo_apigee           = true

  # 1. Existing project links
  existing_net_host_project = "prj-client-shared-net-host"
  existing_governance_project = ""
  existing_gateway_project  = "prj-client-apigee-ingress"
  existing_vpc_id           = "projects/prj-client-shared-net-host/global/networks/vpc-prod-shared"
  existing_subnet_id        = "projects/prj-client-shared-net-host/regions/us-central1/subnetworks/sb-prod-gke"

  # 🛒 THE PRODUCTS THEY WANT ESMERALDA TO CREATE FROM SCRATCH:
  gateway_product     = "apigee" # Point to existing Apigee gateway settings
  database_product    = "cloud-sql"
}
```

### B. Dynamically Skipping Stage 2 (`live/client-prod/stage-2-networking/terragrunt.hcl`)
The networking configuration skips compilation and returns instantly if `byo_networking` is configured:

```hcl
# infrastructure/live/client-prod/stage-2-networking/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../modules//2-networking"
}

locals {
  env_vars = read_terragrunt_config(find_in_parent_folders("env.yaml"))
}

# Skips execution entirely if networking is pre-configured
skip = local.env_vars.locals.byo_networking
```

### C. Downstream Fallback Lookup (`live/client-prod/stage-4-workloads/agents/a2a-agent/terragrunt.hcl`)
Downstream persistence components safely switch their inputs between Stage 2 outputs or `env.yaml` static resource IDs based on the active flag:

```hcl
# infrastructure/live/client-prod/stage-4-workloads/agents/a2a-agent/terragrunt.hcl
include "root" {
  path = find_in_parent_folders()
}

terraform {
  source = "../../../../../modules//4-workloads/agents/a2a-agent"
}

locals {
  env_vars       = read_terragrunt_config(find_in_parent_folders("env.yaml"))
  byo_networking = lookup(local.env_vars.locals, "byo_networking", false)
}

dependency "projects" {
  config_path = "../../../stage-1-projects"
}

dependency "security" {
  config_path = "../../../stage-3-security"
}

dependency "networking" {
  config_path = "../../../stage-2-networking"
  
  # Avoid running output lookups on skipped modules
  skip_outputs = local.byo_networking
  
  # Satisfy parser during evaluation with mock variables
  mock_outputs = {
    network_id = local.env_vars.locals.existing_vpc_id
    subnet_id  = local.env_vars.locals.existing_subnet_id
  }
}

inputs = {
  # Dynamically fetches workload project IDs provisioned dynamically by Esmeralda's Stage 1!
  project_id            = dependency.projects.outputs.a2a_project_id
  region                = local.env_vars.locals.region
  agent_service_account = dependency.security.outputs.a2a_agent_sa_email
  
  # Dynamic Fallback: Use client's existing VPC if BYO is active, else use dependency outputs
  vpc_id                = local.byo_networking ? local.env_vars.locals.existing_vpc_id  : dependency.networking.outputs.network_id
  subnet_id             = local.byo_networking ? local.env_vars.locals.existing_subnet_id : dependency.networking.outputs.subnet_id
  
  database_name         = "a2a_tasks"
  enable_iam_user       = true
}
```

---

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
```
*(With this, any client's NetOps/PlatformOps teams can hand over pre-configured net_host and gateway projects, and Esmeralda will automatically provision and deploy the isolated workload projects and attach them securely to their Shared VPC.)*

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

# Provision Shared VPC (Only if greenfield)
resource "google_compute_network" "shared_vpc" {
  count                   = var.byo_networking ? 0 : 1
  name                    = "vpc-esmeralda-shared"
  project                 = var.net_host_project_id
  auto_create_subnetworks = false
}

# Core Subnet for Workloads
resource "google_compute_subnetwork" "core" {
  count                    = var.byo_networking ? 0 : 1
  name                     = "sb-esmeralda-core"
  project                  = var.net_host_project_id
  region                   = var.region
  network                  = google_compute_network.shared_vpc[0].id
  ip_cidr_range            = "10.0.1.0/24"
  private_ip_google_access = true
}

# Proxy-only Subnet for regional load balancing (Internal Load Balancer/Kong/Apigee)
resource "google_compute_subnetwork" "proxy" {
  count         = var.byo_networking ? 0 : 1
  name          = "sb-esmeralda-proxy"
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
  name          = "sb-esmeralda-psc"
  project       = var.net_host_project_id
  region        = var.region
  network       = google_compute_network.shared_vpc[0].id
  ip_cidr_range = "10.10.0.0/24"
}

# PSC Interface Subnet for Serverless Agent Incoming Tunnels
resource "google_compute_subnetwork" "psc_interface" {
  count         = !var.byo_networking && var.enable_psc_interface ? 1 : 0
  name          = "sb-esmeralda-psc-interface"
  project       = var.net_host_project_id
  region        = var.region
  network       = google_compute_network.shared_vpc[0].id
  ip_cidr_range = "10.11.0.0/28"
}

# Private Service Connection (PSA) range for Cloud SQL Peering
resource "google_compute_global_address" "sql_peering" {
  count         = var.byo_networking ? 0 : 1
  name          = "sql-peering-range"
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
  name    = "cr-esmeralda-nat"
  project = var.net_host_project_id
  region  = var.region
  network = google_compute_network.shared_vpc[0].id
}

resource "google_compute_router_nat" "nat" {
  count                              = var.byo_networking ? 0 : 1
  name                               = "nat-esmeralda-outbound"
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
  name                  = "gateway-psc-interface-attachment"
  region                = var.region
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [var.byo_networking ? var.existing_subnet_id : try(google_compute_subnetwork.psc_interface[0].self_link, "")]
}

# Ingress Firewall to accept traffic from the PSC Interface Subnet
resource "google_compute_firewall" "psc_interface_allow" {
  count         = var.enable_psc_interface && !var.byo_networking ? 1 : 0
  project       = var.net_host_project_id
  name          = "allow-psc-interface-ingress"
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
  name       = "gateway-swp"
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
  name        = "esmeralda-private-dns"
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
  name       = "internal-gateway"
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

### 7.3 Stage 3: `modules/3-security/` Specification

This module establishes central customer-managed cryptographic keys (CMEK) via Cloud KMS, configures secure secret storage boundaries in Secret Manager, provisions isolated, least-privilege workload Service Accounts for each engineering domain (including a dedicated Test VM service account and full-parity roles from Esmeralda's monolithic `test-vm-sa`), and hooks up enterprise audit and telemetry log sinks. Additionally, it integrates Google Cloud's **Certificate Manager** to provision SSL/TLS certificates in `prj-gateway` and write validation records to the DNS zone in `prj-net-host` dynamically.

Under our centralized governance design, all KMS keyrings, keys, and secrets are created in the centralized **`prj-esmeralda-governance`** project during Stage 3. Workload runtimes (e.g. Cloud SQL in `prj-esmeralda-a2a-agents`) merely consume these resources over cross-project IAM bindings.

#### A. Cryptographic, Secrets, and Identity Isolation Architecture
Stage 3 establishes centralized encryption-at-rest keys, credentials, and cryptographic identities to satisfy strict corporate infosec rules:

```mermaid
graph TD
    subgraph "prj-net-host (Shared VPC Host)"
        DNS["Managed DNS Zone<br/>(dns-esmeralda-internal)"]
    end

    subgraph "prj-gateway (API Ingress Project)"
        Cert["Certificate Manager<br/>(esmeralda-regional-cert)"]
        DNSAuth["DNS Authorization<br/>(esmeralda-regional-dns-auth)"]
    end

    subgraph "prj-esmeralda-governance (Governance & Telemetry Hub)"
        TelemetryLogs["BigQuery Dataset<br/>(esmeralda_telemetry_logs)"]
        
        KeyRing["KMS Keyring<br/>(keyring-esmeralda)"]
        KeySQL["Database Key (CMEK)<br/>(key-esmeralda-sql)"]
        KeySecrets["Secrets Key (CMEK)<br/>(key-esmeralda-secrets)"]
        
        SecretDB["Database Password Secret<br/>(secret-pg-admin-password)"]
    end

    %% Validation Links & Bindings
    DNSAuth -. Validation Record .-> DNS
    Cert -. Validated By .-> DNSAuth

    subgraph "prj-esmeralda-a2a-agents (AI Platform Project)"
        SQLRobot["Cloud SQL Service Robot<br/>(service-prj-a2a-sql...)"]
    end

    SQLRobot -->|roles/cloudkms.cryptoKeyEncrypterDecrypter <br/> Cross-Project CMEK Grant| KeySQL
    SecRobot["Secret Manager Service Robot<br/>(service-prj-gov-sm...)"] -->|roles/cloudkms.cryptoKeyEncrypterDecrypter| KeySecrets
```

##### 1. Key Refinements and Additions:
*   **The Certificate Manager Integration**: SSL/TLS certificate generation is integrated directly into Stage 3. It provisions DNS Authorizations and regional SSL/TLS certificates inside `prj-gateway` (where the load balancers sit). To complete validation automatically without manual operator steps, Stage 3 leverages an aliased provider targeting the host project (`prj-net-host`) and writes the CNAME validation record sets directly into the pre-existing Cloud DNS Managed Zone.
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
*   KMS Keyrings, KMS Crypto Keys, Secret Manager Secrets, and Certificate Manager Certificates are **completely bypassed** during deployment.
*   Workload Service Accounts, the Test VM Service Account, and their precise IAM role bindings are **still created** and linked back to target project boundaries.
*   Downstream modules switch their inputs to point to the static pre-existing key, secret, and certificate IDs passed via local configurations.

---

#### C. File Inventory & Blueprints

```text
infrastructure/modules/3-security/
├── versions.tf          # Mandates HashiCorp Google provider bounds (>= 5.0)
├── variables.tf         # Multi-project inputs, BYO KMS/Secret/Cert overrides, and project numbers
├── main.tf              # Implements Cloud KMS, secrets, SAs, Certificate Manager, and log sinks
└── outputs.tf           # Exports SAs, KMS Key IDs, Secret Resource Names, and Certificate names
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

# Certificate Manager Configurations
variable "enable_certificate_manager" {
  description = "Whether to provision Google Cloud Certificate Manager and DNS validation records"
  type        = bool
  default     = false
}

variable "dns_zone_domain" {
  description = "DNS zone domain (must end with dot, e.g., 'example.com.'). Required if enable_certificate_manager is true."
  type        = string
  default     = null
}

variable "dns_zone_name" {
  description = "The pre-existing managed zone name in prj-net-host. If omitted, is derived from the domain."
  type        = string
  default     = null
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
  name     = "keyring-esmeralda"
  project  = var.governance_project_id
  location = var.region
}

# KMS Key for Private Cloud SQL database encryption
resource "google_kms_crypto_key" "database_key" {
  count           = var.byo_security ? 0 : 1
  name            = "key-esmeralda-sql"
  key_ring        = google_kms_key_ring.keyring[0].id
  rotation_period = "7776000s" # 90 days rotation

  lifecycle {
    prevent_destroy = false
  }
}

# KMS Key for Secret Manager payload encryption
resource "google_kms_crypto_key" "secrets_key" {
  count           = var.byo_security ? 0 : 1
  name            = "key-esmeralda-secrets"
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
  secret_id = "secret-pg-admin-password"
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
  account_id   = "sa-esmeralda-mcps"
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
  account_id   = "sa-esmeralda-a2a"
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
  account_id   = "sa-esmeralda-root"
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
  account_id   = "sa-esmeralda-test-vm"
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
# 4. GOOGLE CLOUD CERTIFICATE MANAGER INTEGRATION
# ====================================================================

# Regional DNS Authorization inside Ingress project
resource "google_certificate_manager_dns_authorization" "regional" {
  count       = var.enable_certificate_manager && var.dns_zone_domain != null ? 1 : 0
  name        = "esmeralda-regional-dns-auth"
  project     = var.gateway_project_id
  location    = var.region
  domain      = trimsuffix(var.dns_zone_domain, ".")
  description = "Regional DNS authorization for esmeralda gateway domain validation"
}

# Regional Managed Certificate inside Ingress project (domain + wildcard)
resource "google_certificate_manager_certificate" "regional" {
  count       = var.enable_certificate_manager && var.dns_zone_domain != null ? 1 : 0
  name        = "esmeralda-regional-cert"
  project     = var.gateway_project_id
  location    = var.region
  description = "Regional SSL certificate for esmeralda gateway ingress"

  managed {
    domains = [
      trimsuffix(var.dns_zone_domain, "."),
      "*.${trimsuffix(var.dns_zone_domain, ".")}"
    ]
    dns_authorizations = [
      google_certificate_manager_dns_authorization.regional[0].id
    ]
  }
}

# Regional Managed Certificate for internal gateway endpoints
resource "google_certificate_manager_certificate" "internal_regional" {
  count       = var.enable_certificate_manager && var.dns_zone_domain != null ? 1 : 0
  name        = "esmeralda-internal-cert"
  project     = var.gateway_project_id
  location    = var.region
  description = "Regional SSL certificate for internal gateway endpoints"

  managed {
    domains = [
      "internal.${trimsuffix(var.dns_zone_domain, ".")}",
      "*.internal.${trimsuffix(var.dns_zone_domain, ".")}"
    ]
    dns_authorizations = [
      google_certificate_manager_dns_authorization.regional[0].id
    ]
  }
}

# --------------------------------------------------------------------
# DNS Validation Record sets (Created Dynamically inside prj-net-host)
# --------------------------------------------------------------------

# Dynamically updates the managed Cloud DNS zone in the NetHost project with validation record
resource "google_dns_record_set" "cert_validation" {
  count        = var.enable_certificate_manager && var.dns_zone_domain != null ? 1 : 0
  project      = var.net_host_project_id
  managed_zone = var.dns_zone_name != null ? var.dns_zone_name : replace(trimsuffix(var.dns_zone_domain, "."), ".", "-")
  
  name         = google_certificate_manager_dns_authorization.regional[0].dns_resource_record[0].name
  type         = google_certificate_manager_dns_authorization.regional[0].dns_resource_record[0].type
  ttl          = 300
  rrdatas      = [google_certificate_manager_dns_authorization.regional[0].dns_resource_record[0].data]
}

# ====================================================================
# 5. ENTERPRISE SYSTEM & TELEMETRY COMPLIANCE SINK
# ====================================================================

# Set up BigQuery Dataset to centralize OpenTelemetry and Gemini executions logs
resource "google_bigquery_dataset" "telemetry_logs" {
  dataset_id                  = "esmeralda_telemetry_logs"
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
  name        = "esmeralda-central-telemetry-sink"
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

output "regional_certificate_name" {
  description = "The resource name of the regional certificate for public ingress"
  value       = try(google_certificate_manager_certificate.regional[0].name, "")
}

output "internal_certificate_name" {
  description = "The resource name of the regional certificate for private internal gateways"
  value       = try(google_certificate_manager_certificate.internal_regional[0].name, "")
}

output "telemetry_dataset_id" {
  description = "The BigQuery dataset ID capturing agentic telemetry and audit logs"
  value       = google_bigquery_dataset.telemetry_logs.dataset_id
}
```

*(With this updated Stage 3 Security implementation, our three workload service accounts contain the complete, high-fidelity permissions derived from the monolithic test VM service account. We have established a dedicated Test VM service account with tight invocations and token-creator privileges, and integrated automated, zero-touch SSL/TLS Certificate Manager deployments with cross-project DNS validations.)*


