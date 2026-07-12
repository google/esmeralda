_format_version: "3.0"
_transform: true

services:
%{ for key, endpoint in agent_endpoints ~}
  - name: ${endpoint.logical_name}
    url: ${endpoint.endpoint_url}
    routes:
      - name: ${endpoint.logical_name}-route
        paths:
          - /${endpoint.logical_name}
        strip_path: true
        preserve_host: false
    plugins:
      # Inject the GCP OIDC Identity Token dynamically on upstream calls
      - name: gcp-service-account
        config:
          audience: "${endpoint.audience}"
%{ endfor ~}
