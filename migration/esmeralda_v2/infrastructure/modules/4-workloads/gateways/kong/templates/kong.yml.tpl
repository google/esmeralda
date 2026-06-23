_format_version: "3.0"
_transform: true

services:
%{ for key, endpoint in agent_endpoints ~}
  - name: ${endpoint.logical_name}
    url: ${endpoint.endpoint_url}
    routes:
      - name: ${endpoint.logical_name}-route
        hosts:
          - ${endpoint.logical_name}.esmeralda.internal
        strip_path: true
    plugins:
      # Inject the GCP OIDC Identity Token dynamically on upstream calls
      - name: gcp-service-account
        config:
          audience: "https://us-central1-aiplatform.googleapis.com"
%{ endfor ~}
