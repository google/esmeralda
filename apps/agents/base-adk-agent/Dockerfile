# app/agents/base-adk-agent/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install build dependencies if needed
RUN apt-get update -qqy && apt-get install -qqy --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging manifests first for layer caching
COPY pyproject.toml .

# Install dependencies directly into system python
RUN pip install --no-cache-dir .

# Copy agent application files
COPY . .

# Install Agent Gateway CA root certificates if provided
ARG AGENT_GATEWAY_ROOT_CERTIFICATES
RUN if [ -n "$AGENT_GATEWAY_ROOT_CERTIFICATES" ]; then \
     echo "Installing Agent Gateway root certificates..."; \
     printf "%b" "$AGENT_GATEWAY_ROOT_CERTIFICATES" | awk 'BEGIN {c=0} /BEGIN CERTIFICATE/ {c++} c > 0 { print > "/usr/local/share/ca-certificates/agw-" c ".crt" }'; \
     update-ca-certificates; \
   fi

# Configure SSL/TLS trust paths for Python HTTP libraries, OpenSSL, and gRPC
ENV GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=${AGENT_GATEWAY_ROOT_CERTIFICATES:+/etc/ssl/certs/ca-certificates.crt}
ENV REQUESTS_CA_BUNDLE=${AGENT_GATEWAY_ROOT_CERTIFICATES:+/etc/ssl/certs/ca-certificates.crt}
ENV SSL_CERT_FILE=${AGENT_GATEWAY_ROOT_CERTIFICATES:+/etc/ssl/certs/ca-certificates.crt}

# Expose standard container port
ENV PORT=8080
EXPOSE 8080

# Run the ADK production API server with Reasoning Engine endpoints and telemetry enabled
CMD ["sh", "-c", "exec adk api_server . --host 0.0.0.0 --port ${PORT:-8080} --no-reload --gemini_enterprise_app_name agent --otel_to_cloud"]

