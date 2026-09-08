# app/agents/a2a-agent/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install build dependencies and CA certificate utilities
RUN apt-get update -qqy && apt-get install -qqy --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging manifests first for layer caching
COPY pyproject.toml .

# Install dependencies directly into system python
RUN pip install --no-cache-dir .

# Copy agent application files and entrypoint
COPY . .
RUN chmod +x /app/scripts/entrypoint.sh && cp /app/scripts/entrypoint.sh /entrypoint.sh && cp /app/sitecustomize.py /usr/local/lib/python3.12/site-packages/sitecustomize.py
RUN if [ -f /app/certs/agw-gateway.crt ]; then \
      cp /app/certs/agw-gateway.crt /usr/local/share/ca-certificates/agw-gateway.crt && \
      update-ca-certificates && \
      python3 -c "import certifi; open(certifi.where(), 'a').write('\n# Agent Gateway Root CA\n' + open('/app/certs/agw-gateway.crt').read())"; \
    fi

# Expose standard container port & Root CA Trust Environment Paths
ENV PORT=8080
ENV GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]

# Run the A2A production server
CMD ["python3", "server.py"]

