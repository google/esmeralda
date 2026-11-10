# Apigee Capability Module: `partner-mcp`

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

This module packages and deploys the `apps/apigee/proxies/partner-mcp` bundle when `governance.capabilities.partner_access = "apigee"`, enforcing server-side JSON-RPC `tools/call` scoping against API product attributes and routing southbound through the internal entrypoint with `GoogleIDToken` custom audiences.


With `southbound_enabled = true` it also builds the southbound path the MCP servers need (their Cloud Run ingress only accepts internal load balancers): a PSC NAT subnet (`psc_nat_cidr`) and a PSC service attachment on the entry point forwarding rule, an Apigee endpoint attachment, a private DNS zone `<southbound_host_label>.<apex_domain>` peered to the Apigee org (so TLS hostname verification against the entry point certificate stays strict), a truststore with the internal Root CA, and the `ts-esmeralda-mcp` target server. `kvm-partner-mcp` holds `allowed_mcp_services` (only these hosts are routed) and `apex_domain`.
