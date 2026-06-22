# Plano de Migração Unificado do Esmeralda v2

Bem-vindo ao **Plano de Migração Unificado do Esmeralda (v2)**. 

Este repositório consolidado foi projetado para simplificar drasticamente o processo de migração da infraestrutura monolítica do Esmeralda para uma arquitetura modular, com governança alinhada às melhores práticas de Landing Zone empresariais do Google Cloud e do framework FAST.

Diferente do plano de migração anterior (que exigia alternar constantemente entre guias conceituais e arquivos técnicos dispersos), a **versão v2** organiza todo o conhecimento em **dois documentos mestre totalmente lineares e autocontidos**. Cada documento integra perfeitamente as explicações e diagramas em português com os códigos completos e scripts em inglês prontos para implantação (**Ctrl+C / Ctrl+V**).

## 💎 Revolução na Experiência do Desenvolvedor (DX)
Um dos marcos mais importantes desta migração é a **modernização completa da Experiência do Desenvolvedor (DX)**. No modelo legado, os deploys do Esmeralda exigiam scripts manuais frágeis (`deploy.sh`) e a sincronização manual de arquivos `.env` locais contendo IPs e credenciais em texto claro. 

A nova arquitetura **elimina 100% a necessidade de `deploy.sh` e arquivos `.env`**:
*   **Fim do `deploy.sh`**: O Terragrunt orquestra o deploy de forma totalmente declarativa com `terragrunt run-all apply`. Ele monta um grafo direcionado de dependências (DAG) e executa as criações em paralelo, respeitando o sequenciamento correto sem a necessidade de `sleeps` artificiais ou scripts imperativos em bash.
*   **Fim dos arquivos `.env`**:
    *   **Variáveis Públicas**: Centralizadas em um arquivo `env.yaml` estruturado para cada ambiente.
    *   **Injeção Dinâmica**: URLs de endpoints privados, IPs e recursos dinâmicos são resolvidos e passados automaticamente de um estágio para o outro via blocos `dependency` do Terragrunt, evitando o preenchimento manual de IPs no disco local do desenvolvedor.
    *   **Segredos Protegidos**: Chaves e senhas críticas são guardadas com segurança no Secret Manager (Stage 3) e consumidas dinamicamente em canais privados criptografados.

---

## 🗺️ Estrutura dos Guias Unificados

O plano está dividido em duas grandes áreas de foco:

### 1. 🏢 [Guia 01: Fundações da Plataforma (Estágios 1, 2 e 3)](01_platform_foundations.md)
*   **Escopo**: Provisionamento inicial do ecossistema, redes e segurança corporativa.
*   **Conteúdo Unificado**:
    *   **Estágio 1: Projetos, FinOps e APIs**: Estruturação de 6 projetos isolados, atribuição de faturamento de modelos de IA e despesas fixas vs. variáveis de computação, e toggles BYOInfra.
    *   **Estágio 2: Redes Privadas, DNS e PSC**: Configuração de VPCs, subredes, Cloud NAT, controle de egress com Secure Web Proxy (SWP), Cloud DNS interno e Network Attachment para conexões PSC com Vertex AI.
    *   **Estágio 3: Segurança, CMEK e Identidades**: Gestão centralizada de chaves KMS, Secret Manager, log sinks corporativos estruturados e auditoria estrita de identidades (Service Accounts isoladas e privilégio mínimo).
    *   **HCL Completo**: Arquivos `versions.tf`, `variables.tf`, `main.tf` e `outputs.tf` verbatim de cada estágio.

### 2. 🏗️ [Guia 02: Cargas de Trabalho, Integração & Delivery (Estágio 4 e Estratégia)](02_workloads_and_delivery.md)
*   **Escopo**: Implantação da prateleira de produtos (gateways, ferramentas MCP e agentes) e orquestração do deploy real e testes.
*   **Conteúdo Unificado**:
    *   **Estágio 4: Catálogo de Aplicações de IA**:
        *   **Gateways Ingress Intercambiáveis**: Códigos completos para Apigee X (e população local-exec de KVM para rotas dinâmicas), Kong Gateway no Cloud Run, ou Internal Load Balancer direto.
        *   **Servidores MCP Composíveis**: Empacotamento Artifact Registry e Cloud Run privado de utilitários como o DMS e Calculadoras Financeiras.
        *   **Motores de Raciocínio (ADK Runtimes)**: Uploads de pacotes `.zip` de agentes, buckets de staging e ativação do Vertex AI Reasoning Engine.
    *   **Estratégia Greenfield vs. Brownfield (BYOInfra)**: Arquivos reais de ambiente (`env.yaml`) e receitas de skip do Terragrunt para pular compilações e fazer fallback condicional sobre redes corporativas legadas.
    *   **Database Bootstrap**: O sequenciamento de inicialização seguro de privilégios de acesso do PostgreSQL e do Cloud SQL via Jobs internos à VPC.
    *   **Ecossistema Onboarding DX (Testes Simétricos)**: Scripts de testes offline com Mocks (`test_local.py`) e verificações integradas pós-deploy (`test_remote.py`), integrados via `Makefile`.
    *   **Revolução na DX**: Comparação detalhada mostrando como a nova infraestrutura remove o uso de `deploy.sh` e o caótico controle manual de arquivos `.env`.

---

## 🚀 Como Executar a Migração com Este Guia

A migração foi desenhada de forma sequencial. Para cada fase:
1.  **Leia a introdução em português** no início da seção para compreender o design, os requisitos de governança do cliente e os impactos de FinOps/Segurança.
2.  **Copie o código-fonte em inglês** (HCL do Terraform/Terragrunt ou scripts em Python/Shell) localizado na respectiva seção de implementação.
3.  **Cole o código diretamente na estrutura de arquivos correspondente** em seu repositório de infraestrutura (`infrastructure/modules/` ou `infrastructure/live/`).

Todos os códigos contidos nesta documentação estão **validados, livres de lints e prontos para produção**, garantindo que você tenha um "copiar e colar" seguro e previsível.

---

*Para iniciar a migração das fundações de rede e projetos, acesse:* **[01_platform_foundations.md](01_platform_foundations.md)**
