# app/agents/cx-mortgage-orchestrator/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install build dependencies and CA certificate utilities
RUN apt-get update -qqy && apt-get install -qqy --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# No env-specific certificates are baked in: the Agent Gateway root CA bundle is injected at
# deploy time (AGENT_GATEWAY_ROOT_CERTIFICATES) and installed by /entrypoint.sh, so the same
# image digest can be promoted from dev to prd.

# Copy packaging manifests first for layer caching
COPY pyproject.toml .

# Install dependencies directly into system python
RUN pip install --no-cache-dir .

# Copy agent application files and entrypoint
COPY . .
RUN chmod +x /app/scripts/entrypoint.sh && cp /app/scripts/entrypoint.sh /entrypoint.sh

# Expose standard container port & Root CA Trust Environment Paths
ENV PORT=8080
ENV GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]

# Run the ADK production API server with Reasoning Engine endpoints and telemetry enabled
CMD ["sh", "-c", "exec adk api_server . --host 0.0.0.0 --port ${PORT:-8080} --no-reload --gemini_enterprise_app_name agent --otel_to_cloud"]

