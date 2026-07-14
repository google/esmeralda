_format_version: "3.0"
_transform: true

services:
%{ for key, endpoint in agent_endpoints ~}
  - name: ${endpoint.logical_name}
    url: ${endpoint.endpoint_url}
    routes:
      - name: ${endpoint.logical_name}-host-route
        hosts:
          - ${endpoint.logical_name}.esmeralda.internal
        strip_path: false
        preserve_host: false
      - name: ${endpoint.logical_name}-path-route
        paths:
          - /${endpoint.logical_name}
        strip_path: true
        preserve_host: false
%{ if endpoint.audience != "" && !strcontains(endpoint.audience, "aiplatform.googleapis.com") ~}
    plugins:
      # Inject the GCP OIDC Identity Token dynamically on upstream calls
      - name: gcp-service-account
        config:
          audience: "${endpoint.audience}"
%{ endif ~}
%{ endfor ~}
