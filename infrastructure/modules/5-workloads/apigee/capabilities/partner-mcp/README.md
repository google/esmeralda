# Apigee Capability Module: `partner-mcp`

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

This module packages and deploys the `apps/apigee/proxies/partner-mcp` bundle when `governance.capabilities.partner_access = "apigee"`, enforcing server-side JSON-RPC `tools/call` scoping against API product attributes and routing southbound through the internal entrypoint with `GoogleIDToken` custom audiences.
