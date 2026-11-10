# Apigee Platform Module (`modules/5-workloads/apigee/platform`)

<!-- Adapted from github.com/ricardosmh/apigeex-terraform-deployment (Apache-2.0) -->

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

## Overview

This module provisions the foundational Apigee X control plane and Private Service Connect (PSC) networking bridge required when one or more Apigee capabilities are enabled in `env.yaml`:

1. **Non-Peering PSC Organization (`byo_apigee_org = false` default)**:
   - Provisions `google_apigee_organization` with `disable_vpc_peering = true` (no `/22` VPC peering reservation required), `runtime_type = "CLOUD"`, configurable `billing_type` (`PAYG`, `EVALUATION`, or `SUBSCRIPTION`), and `retention = "MINIMUM"` on paid organizations.
   - Supports optional Cloud KMS customer-managed encryption keys (`enable_cmek = true`) and Analytics add-on configuration (`enable_analytics`: the environment's PAYG Analytics add-on that feeds the console dashboards and the stats API; `enable_advanced_api_ops`: the separate org-level Advanced API Ops add-on, off by default).
   - Supports Bring-Your-Own Apigee organization mode (`byo_apigee_org = true`) via `existing_org_id` and `existing_instance_service_attachment`.
2. **Instance, Environment & Environment Group**:
   - Provisions `google_apigee_instance` with PSC `consumer_accept_list`, `google_apigee_environment` (`BASE`, `INTERMEDIATE`, or `COMPREHENSIVE`), `google_apigee_envgroup`, and their attachments.
3. **Northbound Entrypoint Contract (Internal ALB -> Apigee)**:
   - Provisions a regional `PRIVATE_SERVICE_CONNECT` Network Endpoint Group targeting the Apigee instance service attachment and a regional `INTERNAL_MANAGED` HTTPS backend service (`timeout_sec = 300`).
   - Exposes `backend_service_id` and `routes` matching the `modules/5-workloads/entrypoint` gateway contract.
4. **Southbound Entrypoint Target Server (Apigee -> Entrypoint ILB)**:
   - When `entrypoint_service_attachment` is provided, provisions `google_apigee_endpoint_attachment` and `google_apigee_target_server` (`ts-esmeralda-entrypoint`) over HTTPS (port `443`) with strict TLS validation (`s_sl_info.enabled = true`, `ignore_validation_errors = false`).
5. **Proxy Runtime Identity**:
   - Provisions `sa-esmeralda-apigee-proxy-<env>` and grants `roles/iam.serviceAccountTokenCreator` to the Apigee Service Agent (`service-<project_number>@gcp-sa-apigee.iam.gserviceaccount.com`) so deployed proxies can mint upstream `GoogleAccessToken` and `GoogleIDToken` credentials.
