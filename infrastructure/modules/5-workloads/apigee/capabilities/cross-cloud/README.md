# Apigee Capability Module: `cross-cloud`

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

Outbound multi-cloud egress from Esmeralda agents to registered external endpoints is handled natively by Agent Gateway. When `governance.capabilities.cross_cloud = "apigee"`, this module configures inbound cross-cloud caller governance (trusted external OIDC/JWT issuer metadata in `kvm-cross-cloud-issuers` for validating and exchanging external identities before forwarding to internal entrypoints).
