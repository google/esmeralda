# Apigee Gateway Wrapper (`modules/5-workloads/gateways/apigee`)

Agent Gateway is Esmeralda's governance plane for agent traffic. Apigee can optionally add API-management capabilities, such as partner access, API products and token quotas, through independent modules selected per capability in `env.yaml`.

This module delegates to [`modules/5-workloads/apigee/platform`](../../apigee/platform/README.md) so callers using the `modules/5-workloads/gateways/*` contract receive the PSC-backed Apigee platform outputs (`backend_service_id` and `routes`). For API products and consumer credentials, see [`modules/5-workloads/apigee/products`](../../apigee/products/README.md).
