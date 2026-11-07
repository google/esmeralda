# Envoy gateway image: environment-neutral, promoted by digest like every other image.
# The bootstrap is rendered by infrastructure/modules/5-workloads/gateways/envoy/config and mounted from
# Secret Manager at /etc/envoy/envoy.yaml; Cloud Run passes the arguments. Envoy >= 1.39 is required
# (gcp_authn access tokens).
FROM envoyproxy/envoy:distroless-v1.40.2@sha256:09ca6f2140a61ad8df11779a4a30d52b48433d444466328494f9188c9c35eded

EXPOSE 8080
