# Apigee Capability Module: `llm-gateway`

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

This module packages and deploys the `apps/apigee/proxies/llm-gateway` proxy (base path `/llm`) when `governance.capabilities.model_gateway = "apigee"`. It is the agent → model hop: agents send their Gemini calls to `https://<gateway_host>/llm` (`ESMERALDA_MODEL_GATEWAY_URL`), and the proxy:

1. verifies the caller's Google-signed ID token (`VerifyJWT`, Google JWKS, audience `https://<gateway_host>`);
2. allows only registered callers (`allowed_callers`), projects (`model_projects`), locations and models (fail closed);
3. enforces an `LLMTokenQuota` per verified caller and counts input + output tokens from the JSON response or the SSE stream;
4. calls Vertex AI with the proxy service account's own access token (`GoogleAccessToken`);
5. records Apigee analytics (`DataCapture`, collectors from the `usage-metering` module) and one `esmeralda.usage` `llm.call` record per call (`ML-interaction`).

It writes the `kvm-llm-gateway` environment KVM and grants the proxy service account `roles/aiplatform.user` on `model_projects`.

Requirements: `usage_metering = "apigee"` (data collectors) and an `INTERMEDIATE` or `COMPREHENSIVE` Apigee environment (`LLMTokenQuota`). The entry point must route `<gateway_host>` to the Apigee backend.
