# Apigee Capability Module: `token-quota`

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

This module packages and deploys the `apps/apigee/sharedflows/token-quota` shared flow when `governance.capabilities.token_quota = "apigee"`, enforcing and incrementing per-consumer LLM token limits across total tokens (`input + output`).
