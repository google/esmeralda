# syntax=docker/dockerfile:1
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Esmeralda console: one image, Node at build time only.
#   1. web:     builds the React app (package-lock.json, no install scripts) into static files;
#   2. deps:    installs the BFF's runtime dependencies, hash-pinned, from the workspace uv.lock;
#   3. runtime: Python BFF as a non-root user, serving /api/* and the static files.
# The resolved catalog (catalog/, checked against catalog/.resolved/<env>.json at startup) is baked in:
# a catalog change means a rebuild, as for the agents. No Secret Manager, no runtime download.
#
# Build from the repository root (the context is filtered by Dockerfile.dockerignore next to this file):
#   docker build -f apps/frontends/esmeralda-ui/Dockerfile .
# Every base image is pinned by digest (Dependabot proposes bumps).

FROM node:24.14.0-slim@sha256:d8e448a56fc63242f70026718378bd4b00f8c82e78d20eefb199224a4d8e33d8 AS web
WORKDIR /web
COPY apps/frontends/esmeralda-ui/web/package.json apps/frontends/esmeralda-ui/web/package-lock.json apps/frontends/esmeralda-ui/web/.npmrc ./
RUN npm ci
COPY apps/frontends/esmeralda-ui/web/ ./
RUN npm run build

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS deps
COPY --from=ghcr.io/astral-sh/uv:0.9.30@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /bin/uv
WORKDIR /src
# The workspace manifests and the single lock: the export resolves nothing, it reads uv.lock.
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY apps/ apps/
RUN uv export --frozen --package esmeralda-ui --no-dev --no-emit-workspace --quiet -o /tmp/requirements.lock && \
    uv venv /app/.venv && \
    uv pip install --python /app/.venv/bin/python --require-hashes --no-cache -r /tmp/requirements.lock

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN groupadd -g 65532 nonroot && \
    useradd -u 65532 -g 65532 -M -s /bin/false nonroot
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
# The esmeralda package and the BFF, from source (same files as the workspace; no build backend needed).
COPY packages/esmeralda/src/esmeralda /app/src/esmeralda
COPY apps/frontends/esmeralda-ui/api/src/esmeralda_ui /app/src/esmeralda_ui
COPY catalog /app/repo/catalog
COPY contracts /app/repo/contracts
COPY --from=web /web/dist /app/web

ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ESMERALDA_UI_MODE=cloud \
    ESMERALDA_UI_CATALOG_ROOT=/app/repo \
    ESMERALDA_UI_STATIC_DIR=/app/web

USER 65532:65532
EXPOSE 8080
# Cloud mode: refuses to start without ESMERALDA_UI_IAP_AUDIENCE; every request needs a valid IAP JWT.
CMD ["/app/.venv/bin/python", "-m", "esmeralda_ui"]
