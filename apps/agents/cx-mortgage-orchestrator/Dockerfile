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
# deploy time (AGENT_GATEWAY_ROOT_CERTIFICATES) and installed by `esmeralda run` (the entrypoint),
# so the same image digest can be promoted from dev to prd.

# Install the exact, hash-pinned dependency set exported from the committed workspace uv.lock
# (generated as requirements.lock by `make build-*`), instead of re-resolving at build time.
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# The esmeralda runtime library (packages/esmeralda): `make build-*` builds its wheel into dist/.
# Its own dependencies are already pinned in requirements.lock, hence --no-deps.
COPY dist/ /tmp/dist/
RUN pip install --no-cache-dir --no-deps /tmp/dist/esmeralda-*.whl && rm -rf /tmp/dist

# Copy agent application files
COPY . .

# Expose standard container port & Root CA Trust Environment Paths
ENV PORT=8080
ENV GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
EXPOSE 8080

# Installs the Agent Gateway root CA, then execs CMD.
ENTRYPOINT ["esmeralda", "run", "--"]

# Serve the agent (framework and agent card from agent.yaml), exporting telemetry to Google Cloud.
# `make serve` runs the same command locally.
CMD ["esmeralda", "serve", "--otel-to-cloud"]

