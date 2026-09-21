# app/agents/a2a-agent/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install build dependencies and CA certificate utilities
RUN apt-get update -qqy && apt-get install -qqy --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install root certificates according to official GCP BYOC Agent Gateway specification
ARG AGENT_GATEWAY_ROOT_CERTIFICATES
RUN if [ -n "$AGENT_GATEWAY_ROOT_CERTIFICATES" ]; then \
      echo "Installing Agent Gateway root certificates from build arg..."; \
      printf "%b" "$AGENT_GATEWAY_ROOT_CERTIFICATES" | awk 'BEGIN {c=0} /BEGIN CERTIFICATE/ {c++} c > 0 { print > "/usr/local/share/ca-certificates/agw-" c ".crt" }'; \
      update-ca-certificates; \
    fi

# Copy packaging manifests first for layer caching
COPY pyproject.toml .

# Install dependencies directly into system python
RUN pip install --no-cache-dir .

# Copy agent application files and entrypoint
COPY . .
RUN if [ -f /app/certs/agw-gateway.crt ]; then \
      cp /app/certs/agw-gateway.crt /usr/local/share/ca-certificates/agw-gateway.crt && \
      update-ca-certificates; \
    fi && \
    python3 -c "import certifi, glob; [open(certifi.where(), 'a').write('\n# Agent Gateway Root CA\n' + open(f).read()) for f in glob.glob('/usr/local/share/ca-certificates/agw*.crt')]" && \
    chmod +x /app/scripts/entrypoint.sh && cp /app/scripts/entrypoint.sh /entrypoint.sh

# Expose standard container port & Root CA Trust Environment Paths
ENV PORT=8080
ENV GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]

# Run the A2A production server
CMD ["python3", "server.py"]

