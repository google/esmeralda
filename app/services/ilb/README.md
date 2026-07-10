# Esmeralda Internal Routing Broker (ILB Proxy)
This service acts as the internal traffic routing plane and reverse proxy for Google Cloud Run and Vertex AI Reasoning Engine workloads inside the Shared VPC.

## Architecture
- Evaluates `AGENT_ENDPOINTS_JSON` environment variable to dynamically map logical agent paths (`/agent/{name}/...`).
- Automatically injects Google Cloud IAM bearer tokens when communicating across private Shared VPC endpoints.
