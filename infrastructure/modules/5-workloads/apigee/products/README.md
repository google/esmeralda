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

## Rotating a consumer's API key

Each consumer has a rotation keeper, `consumers.<name>.key_rotation` (default `""`). To rotate the key, set it to a new value (the rotation date is a good choice) in the environment's `apigee/products` unit and apply with destroy allowed:

```bash
uv run esmeralda plan apigee/products --env dev
uv run esmeralda apply apigee/products --env dev --allow-destroy
```

The plan replaces the consumer's `google_apigee_developer_app` (Apigee issues a new key) and its `google_secret_manager_secret_version` (the new key becomes `latest`, the version holding the old key is destroyed). The old key stops working as soon as the app is replaced, so callers that read `latest` from Secret Manager (such as `scripts/test_partner.sh` and `esmeralda verify --partner`) pick up the new key on their next run. Callers that cached the key (for example `ESMERALDA_PARTNER_API_KEY` in a local env file) must be updated.

A keeper's first apply never rotates: adding a consumer, or upgrading an environment to a module version with keepers, creates them without replacing any app. Analytics and attribution key on the app name, which does not change.
