# Apigee Capability Module: `partner-agents`

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

This module packages and deploys the `apps/apigee/proxies/partner-agents` bundle when `governance.capabilities.partner_access = "apigee"` and populates the `kvm-partner-agents` environment KeyValueMap with catalog entries marked `expose_to_partners = true`.
