# app/agents/a2a-agent/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install build dependencies if needed for compiling asyncpg / grpcio wheels
RUN apt-get update -qqy && apt-get install -qqy --no-install-recommends \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy packaging manifests first for layer caching
COPY pyproject.toml .

# Install dependencies directly into system python
RUN pip install --no-cache-dir .

# Copy agent application files
COPY . .

# Configure SSL/TLS trust paths for Python HTTP libraries, OpenSSL, and gRPC
ENV GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Make entrypoint script executable and configure ENTRYPOINT
RUN chmod +x ./scripts/entrypoint.sh

# Expose standard container port
ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["./scripts/entrypoint.sh"]

# Run the A2A production server
CMD ["python3", "server.py"]
