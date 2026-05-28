<div align="center">
  <img src="assets/esmeralda_logo.png" alt="Esmeralda logo" width="350" />
  <h1><code>esmeralda</code></h1>
  <p>An opinionated, commercial-grade blueprint designed to accelerate the path to production for AI Agents.</p>

  <p>
    <a href="#-the-before-you-start-checklist">Checklist</a> &nbsp;&nbsp;|&nbsp;&nbsp;
    <a href="#-quick-start-monorepo-mode">Quick Start</a> &nbsp;&nbsp;|&nbsp;&nbsp;
    <a href="#-modular-deployment-silo-mode">Architecture</a> &nbsp;&nbsp;|&nbsp;&nbsp;
    <a href="#-troubleshooting">Troubleshooting</a>
  </p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License" /></a>
    <a href="https://cloud.google.com/"><img src="https://img.shields.io/badge/Cloud-Google_Cloud-4285F4?style=flat&logo=google-cloud&logoColor=white" alt="Google Cloud" /></a>
    <a href="https://www.terraform.io/"><img src="https://img.shields.io/badge/IAC-Terraform-623CE4?style=flat&logo=terraform&logoColor=white" alt="Terraform" /></a>
    <a href="https://cloud.google.com/vertex-ai"><img src="https://img.shields.io/badge/AI-Vertex_AI-00C1FF?style=flat&logo=google-cloud&logoColor=white" alt="Vertex AI" /></a>
  </p>
</div>

---

### What does ESMERALDA stand for?
ESMERALDA is an opinionated, commercial-grade blueprint designed to accelerate the path to production for AI Agents. The acronym represents the framework's four core pillars:

*   **ES – Enterprise Standard:** A zero-trust, secure-by-default foundational architecture built for enterprise compliance and scale.
*   **ME – Multi-agent Engine:** The core orchestration layer utilizing Apigee to govern inter-agent communication and secure egress via Private Service Connect (PSC).
*   **RAL – Reasoning & Action Layer:** Powered by Gemini 2.5 Flash and Model Context Protocol (MCP) standards for complex reasoning and structured JSON execution.
*   **DA – Deployment Accelerator:** A "business-in-a-box" toolkit featuring pre-packaged SOWs, cost simulators, and a Terraform-driven CI/CD pipeline.

---

## 📖 Overview

**ESMERALDA** is a code-first deployment blueprint designed to bridge the gap between "prototype" and "production" for AI Agents. It applies software engineering best practices—like modularity, Infrastructure as Code (IaC), and automated discovery—to the **Vertex AI Reasoning Engine** ecosystem. It integrates advanced security mechanisms such as Model Armor for prompt/response safety and Envoy `ext_proc` sidecars for DLP redaction.

## ✅ The "Don't come crying to me later" Checklist

To save you time and tears, make sure you've covered these points.

**1. Run from Cloud Shell (Strongly Recommended)**

The easiest and fastest way to deploy is using **Google Cloud Shell**.

*   **Why?** All required tools (`gcloud`, `terraform`, `python`) are pre-installed and your authentication is already configured.

**2. If Running Locally, Check Your Tools**

If you prefer running from your local machine, you must have:
*   Google Cloud SDK (`gcloud`)
    *   **Note**: To install alpha-level commands, run `gcloud components install alpha` or `sudo apt-get install google-cloud-sdk-alpha` if you installed via `apt`.
    *   **Note**: To enable GKE cluster access, run `gcloud components install gke-gcloud-auth-plugin` or `sudo apt-get install google-cloud-sdk-gke-gcloud-auth-plugin`.
*   Terraform (`>= 1.5`)
*   Python (`>= 3.10`)

**3. Authenticate Correctly**

This is the most common point of failure. Terraform needs permission to act on your behalf.
*   **Run this first:**
    ```bash
    gcloud auth login
    ```
*   **And then run this:** This command is critical for Terraform.
    ```bash
    gcloud auth application-default login
    ```

## 🚀 Quick Start (Monorepo Mode)

If you are a single developer deploying everything from this repository:

1.  **Configure**:
    ```bash
    cp env.example .env
    # Fill in your ORG_ID and BILLING_ACCOUNT in the new .env file.
    ```
2.  **Deploy All**:
    ```bash
    make all
    ```

## 🏗️ Modular Deployment (Silo Mode)

The true power of this framework is its modularity. Each folder is **self-contained** and can be moved to its own Git repository to be managed by different teams.

The beauty of this modular design is its simplicity. Each folder is a self-contained unit: **just `cd` into the directory you want to deploy and run its specific `make` command.**

### 1. Infrastructure (`infrastructure/`)

Manages the Google Cloud project, IAM, VPC networks, and security guardrails (Model Armor).
*   **Configure**: `cp env.example .env`
*   **To deploy**: `cd infrastructure && make infra`
*   **CI/CD**: See `infrastructure/cloudbuild.yaml`

### 2. Tools (`tools/`)

Deploys Model Context Protocol servers to Cloud Run, automatically registering them to the Agent Registry and API Hub by dynamically discovering the live endpoints. Includes demo tools such as Corporate Email, Income Verification API, and Legacy DMS.
*   **Configure**: `cp env.example .env`
*   **To deploy**: `cd tools && make tools`
*   **CI/CD**: See `tools/cloudbuild.yaml`

### 3. Agents (`agents/`)

Deploys Vertex AI Reasoning Engines with secure Private Service Connect (PSC) attachments.
*   **Important**: Before deploying, set your gcloud project context: `gcloud config set project [YOUR_NEWLY_CREATED_PROJECT_ID]`. This is required for auto-discovery to work.
*   **Configure**: `cp env.example .env`
*   **To deploy**: `cd agents && make agent`
*   **CI/CD**: See `agents/cloudbuild.yaml`

## 🛠️ Troubleshooting

If you hit an error, don't panic. It's usually one of two things.

#### **Error 1: `invalid token` or `permission denied` on Billing Account**

This is likelly an authentication issue. Your local credentials have expired.
*   **The Fix**: Re-authenticate.
    ```bash
    gcloud auth application-default login
    ```

## 🏛️ Key Architectural Features

*   **Zero-Dependency Silos**: Each folder has its own `Makefile`, `env.example`, and deployment logic. No shared script files.
*   **Context Discovery**: Scripts are "intelligent"—if a variable is missing from `.env`, they will attempt to discover it using the `gcloud` CLI.
*   **Safe Resume**: State is automatically saved to `.env`. If a step fails, you can fix it and rerun **only that silo**.

## 🔍 Usage

### Curl Query Examples

Once deployed, you can interact with your agent using the following commands. Replace the placeholders with your actual values.

#### 1. Create a session

```bash
cURL \
-H "Authorization: Bearer $(gcloud auth print-access-token)" \
-H "Content-Type: application/json" \
https://<YOUR_PROJECT_REGION>[-aiplatform.googleapis.com/v1/projects/](https://-aiplatform.googleapis.com/v1/projects/)<YOUR_PROJECT_ID_FROM_TERRAFORM>/locations/<LOCATION>/reasoningEngines/<REASONING_ENGINE_ID>:query -d '{"class_method": "async_create_session", "input": {"user_id": "USER_ID"},}'