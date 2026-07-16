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

# Expose standard container port
ENV PORT=8080
EXPOSE 8080

# Run the ADK production API server with Reasoning Engine endpoints and telemetry enabled
CMD ["sh", "-c", "exec adk api_server . --host 0.0.0.0 --port ${PORT:-8080} --no-reload --gemini_enterprise_app_name agent --otel_to_cloud"]

