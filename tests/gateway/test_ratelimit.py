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

"""Rate limiting (H9; UC13): N requests per minute per X-API-Key per instance (120 in production, 5 here)."""

import uuid

MCP_HOST = "legacy-dms.esmeralda.internal"
LIMIT = 5


def send(gateway, count: int, headers: dict | None = None) -> list[int]:
    return [gateway.post(MCP_HOST, "/mcp", json={}, headers=headers or {}).status_code for _ in range(count)]


def key(name: str) -> dict[str, str]:
    """A fresh X-API-Key, so every test (and every re-run in the same session) starts with a full bucket."""
    return {"X-API-Key": f"{name}-{uuid.uuid4().hex[:8]}"}


def test_requests_over_the_per_key_limit_get_429(limited_gateway, mcp_upstream):
    assert send(limited_gateway, LIMIT + 1, key("agent-over-limit")) == [200] * LIMIT + [429]


def test_each_api_key_has_its_own_bucket(limited_gateway, mcp_upstream):
    send(limited_gateway, LIMIT + 1, key("agent-a"))

    assert send(limited_gateway, 1, key("agent-b")) == [200]


def test_many_active_keys_do_not_reset_each_other(limited_gateway, mcp_upstream):
    """Envoy keeps only 20 per-key buckets unless told otherwise, recycling the least recently used."""
    exhausted = key("agent-exhausted")
    send(limited_gateway, LIMIT + 1, exhausted)

    for index in range(25):
        send(limited_gateway, 1, key(f"agent-{index}"))

    assert send(limited_gateway, 1, exhausted) == [429]


def test_requests_without_api_key_share_the_default_bucket(start_variant, mcp_upstream):
    """On a gateway of its own: every request without a key draws from one bucket, so it can't be reused."""
    gateway = start_variant(f"limited-default-{uuid.uuid4().hex[:8]}", rate_limit_per_minute=LIMIT)

    assert send(gateway, LIMIT + 1) == [200] * LIMIT + [429]


def test_rate_limited_responses_carry_retry_after(limited_gateway, mcp_upstream):
    retry = key("agent-retry")
    send(limited_gateway, LIMIT, retry)

    response = limited_gateway.post(MCP_HOST, "/mcp", json={}, headers=retry)

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1
