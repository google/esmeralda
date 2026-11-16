# Apigee Capability Module: `llm-gateway`

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

This module packages and deploys the `apps/apigee/proxies/llm-gateway` proxy (base path `/llm`) when `governance.capabilities.model_gateway = "apigee"`. It is the agent → model hop: agents send their Gemini calls to `https://<gateway_host>/llm` (`ESMERALDA_MODEL_GATEWAY_URL`), and the proxy:

1. verifies the caller's ID token (`VerifyJWT`, audience `https://<gateway_host>`): an agent's unbound Agent Identity ID token, checked against its trust domain's issuer and JWKS on Security Token Service (derived from `agent_principals`), or a Google service account ID token (`allowed_callers`, e.g. the test VM);
2. allows only registered callers (`agent_principals`, `allowed_callers`), projects (`model_projects`), locations and models (fail closed);
3. enforces an `LLMTokenQuota` per verified caller and counts input + output tokens from the JSON response or the SSE stream;
4. calls Vertex AI with the proxy service account's own access token (`GoogleAccessToken`);
5. records Apigee analytics (`DataCapture`, collectors from the `usage-metering` module) and one `esmeralda.usage` `llm.call` record per call (`ML-interaction`), with the verified principal and the calling agent as `workload.name`.

It writes the `kvm-llm-gateway` environment KVM and grants the proxy service account `roles/aiplatform.user` on `model_projects`.

Requirements: `usage_metering = "apigee"` (data collectors) and an `INTERMEDIATE` or `COMPREHENSIVE` Apigee environment (`LLMTokenQuota`). The entry point must route `<gateway_host>` to the Apigee backend, and the catalog must declare the gateway as a `managed: false` target with the agents as callers, so the Central Agent Gateway lets them reach it.
