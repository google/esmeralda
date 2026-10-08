# app/agents/ai-coe-mortgage-specialist/Dockerfile
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

# Install the exact, hash-pinned dependency set exported from the committed workspace uv.lock
# (generated as requirements.lock by `make build-*`), instead of re-resolving at build time.
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

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

# Run the A2A production server
CMD ["python3", "server.py"]

