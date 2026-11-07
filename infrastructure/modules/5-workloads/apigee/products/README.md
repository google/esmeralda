# Apigee API Products & Consumers Module (`modules/5-workloads/apigee/products`)

<!-- Adapted from github.com/ricardosmh/adk-apigee-multiagent-demo (Apache-2.0) -->

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

## Overview

This module manages Apigee API products, partner/consumer developer applications, and Secret Manager synchronization declaratively in Terraform:

1. **API Products (`google_apigee_api_product`)**:
   - Binds products to the target Apigee environment, allowed proxies, and resource paths.
   - Attaches custom attributes consumed at runtime by Esmeralda's Apigee capability policies and shared flows:
     - `access = "private"`
     - `allowed_mcp_tools` (comma-separated tool allowlist enforced server-side at the gateway)
     - `llm_quota_limit`, `llm_quota_interval`, and `llm_quota_time_unit` (enforced by the `token-quota` capability's `LLMTokenQuota` policies)
2. **Developers & Consumer Apps (`google_apigee_developer`, `google_apigee_developer_app`)**:
   - Registers partner/consumer developers and apps subscribed to one or more API products.
   - Sets the `owner_identity` custom attribute on each app to bind API keys to verified caller identities.
3. **Secret Manager Bridge (`google_secret_manager_secret`, `google_secret_manager_secret_version`)**:
   - Syncs each consumer app's generated API key into Secret Manager (`apigee-consumer-key-<consumer_name>-<env>`) with `sensitive(...)` handling and grants `roles/secretmanager.secretAccessor` to configured runtime or test service accounts.
