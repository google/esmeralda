# app/agents/a2a-agent/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install build dependencies if needed for compiling asyncpg / grpcio wheels
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

# Run the A2A production server
CMD ["python3", "server.py"]

