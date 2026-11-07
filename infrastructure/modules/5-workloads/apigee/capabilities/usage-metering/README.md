# Apigee Capability Module: `usage-metering`

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

This module packages and deploys the `apps/apigee/sharedflows/usage-metering` shared flow and provisions the Apigee analytics data collectors (`dc_llm_model`, `dc_prompt_tokens`, `dc_candidates_tokens`, `dc_total_tokens`, `dc_consumer_app`) when `governance.capabilities.usage_metering = "apigee"`.
