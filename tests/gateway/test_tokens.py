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

"""Token swap (H1, H6; UC8, UC9): upstreams get the gateway's own token for their audience, fetched once."""

from fakes import FakeMetadataServer, bearer, jwt_claims

MCP_HOST = "legacy-dms.esmeralda.internal"


def test_mcp_upstream_receives_id_token_for_its_audience(gateway, mcp_upstream):
    gateway.post(MCP_HOST, "/mcp", json={})

    assert jwt_claims(bearer(mcp_upstream.last))["aud"] == mcp_upstream.url


def test_caller_authorization_is_replaced(gateway, mcp_upstream):
    gateway.post(MCP_HOST, "/mcp", json={}, headers={"Authorization": "Bearer caller-id-token"})

    token = bearer(mcp_upstream.last)
    assert token != "caller-id-token"
    assert "caller-id-token" not in mcp_upstream.last.headers["authorization"]
    assert jwt_claims(token)["aud"] == mcp_upstream.url


def test_caller_tokens_never_travel_next_to_the_gateway_token(gateway, mcp_upstream):
    """Even two caller Authorization headers leave exactly one header, holding only the gateway's token."""
    callers = [("Authorization", "Bearer caller-a"), ("Authorization", "Bearer caller-b")]

    assert gateway.raw("POST", MCP_HOST, "/mcp", headers=callers).status == 200

    assert jwt_claims(bearer(mcp_upstream.last))["aud"] == mcp_upstream.url
    assert not [value for _, value in mcp_upstream.last.header_lines if "caller-" in value]


def test_each_upstream_gets_a_token_for_its_own_audience(gateway, mcp_upstream, upstreams):
    email = upstreams["corporate-email"]
    email.reset()

    gateway.post(MCP_HOST, "/mcp", json={})
    gateway.post("corporate-email.esmeralda.internal", "/mcp", json={})

    assert jwt_claims(bearer(mcp_upstream.last))["aud"] == mcp_upstream.url
    assert jwt_claims(bearer(email.last))["aud"] == email.url


def test_id_tokens_are_cached_per_audience(gateway, mcp_upstream, metadata):
    gateway.post(MCP_HOST, "/mcp", json={})  # warm the cache
    before = metadata.id_calls[mcp_upstream.url]
    assert before >= 1, "the ID-token path was never exercised"

    for _ in range(20):
        assert gateway.post(MCP_HOST, "/mcp", json={}).status_code == 200

    assert metadata.id_calls[mcp_upstream.url] == before
    assert metadata.unexpected == []


SPECIALIST_HOST = "ai-coe-mortgage-specialist.esmeralda.internal"


def test_specialist_upstream_receives_access_token(gateway, specialist_upstream):
    gateway.get(SPECIALIST_HOST, "/v1/card", headers={"Authorization": "Bearer caller-id-token"})

    assert bearer(specialist_upstream.last) == FakeMetadataServer.ACCESS_TOKEN


def test_access_tokens_are_cached(gateway, specialist_upstream, metadata):
    gateway.get(SPECIALIST_HOST, "/v1/card")  # warm the cache
    before = metadata.access_calls
    assert before >= 1, "the access-token path was never exercised"

    for _ in range(20):
        assert gateway.get(SPECIALIST_HOST, "/v1/card").status_code == 200

    assert metadata.access_calls == before
