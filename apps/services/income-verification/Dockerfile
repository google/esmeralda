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

# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
FROM python:3.12-slim

# Pinned uv release (never :latest) for reproducible, supply-chain-safe builds.
COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /uvx /bin/

RUN groupadd -g 65532 nonroot && \
    useradd -u 65532 -g 65532 -s /bin/false nonroot

WORKDIR /app

# Install the exact, hash-pinned dependency set exported from the committed workspace uv.lock
# (generated as requirements.lock by `make build-*`), instead of re-resolving at build time.
COPY --chown=65532:65532 requirements.lock ./
RUN uv venv .venv && uv pip install --python .venv/bin/python --require-hashes --no-cache -r requirements.lock

COPY --chown=65532:65532 . .

USER 65532:65532

EXPOSE 8080

CMD [".venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
