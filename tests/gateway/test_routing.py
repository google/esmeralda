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

"""Routing (H1–H3, H5, H8; UC1, UC2, UC4, UC6, UC11, UC12, UC25): every host and path reaches the right place."""

import pytest

MCP_HOST = "legacy-dms.esmeralda.internal"
SPECIALIST_HOST = "ai-coe-mortgage-specialist.esmeralda.internal"
TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_mcp_route_keeps_path_and_rewrites_host(gateway, mcp_upstream):
    response = gateway.post(MCP_HOST, "/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})

    assert response.status_code == 200
    assert mcp_upstream.last.method == "POST"
    assert mcp_upstream.last.path == "/mcp"
    assert mcp_upstream.last.headers["host"] == "localhost"
    assert mcp_upstream.last.body == response.request.content


def test_each_mcp_host_reaches_its_own_upstream(gateway, mcp_upstream, upstreams):
    email = upstreams["corporate-email"]
    email.reset()

    gateway.post("corporate-email.esmeralda.internal", "/mcp", json={})

    assert len(email.requests) == 1
    assert mcp_upstream.requests == []


def test_caller_headers_reach_upstream_unchanged(gateway, mcp_upstream):
    sent = {"X-API-Key": "ai-coe-mortgage-specialist", "User-Auth-Token": "user-token-1", "traceparent": TRACEPARENT}

    gateway.post(MCP_HOST, "/mcp", json={}, headers=sent)

    for name, value in sent.items():
        assert mcp_upstream.last.headers[name.lower()] == value


def test_request_paths_are_normalized_before_routing(gateway, mcp_upstream):
    """Dot segments and repeated slashes are resolved first, so no request can climb out of a base path."""
    for raw_path in ("/x/../mcp", "/x/%2e%2e/mcp", "//mcp", "/./mcp"):
        assert gateway.raw("GET", MCP_HOST, raw_path).status == 200, raw_path
        assert mcp_upstream.last.path == "/mcp", raw_path


def test_encoded_slashes_are_rejected(gateway, mcp_upstream):
    for raw_path in ("/x/..%2Fmcp", "/x/..%5Cmcp"):
        assert gateway.raw("GET", MCP_HOST, raw_path).status == 400, raw_path
    assert mcp_upstream.requests == []


def test_host_with_trailing_dot_routes_like_bare_host(gateway, mcp_upstream):
    assert gateway.post(f"{MCP_HOST}.", "/mcp", json={}).status_code == 200
    assert mcp_upstream.last.path == "/mcp"


def test_callers_cannot_steer_envoy_with_x_envoy_headers(gateway, mcp_upstream):
    """Caller-sent x-envoy-* headers are dropped: no shorter timeout, no extra retries."""
    mcp_upstream.delay_s = 0.5
    steering = {"x-envoy-upstream-rq-timeout-ms": "1", "x-envoy-max-retries": "10", "x-envoy-retry-on": "5xx"}

    response = gateway.post(MCP_HOST, "/mcp", json={}, headers=steering)

    assert response.status_code == 200
    assert len(mcp_upstream.requests) == 1
    assert not set(steering) & set(mcp_upstream.last.headers)


def test_host_with_port_routes_like_bare_host(gateway, mcp_upstream):
    response = gateway.post(f"{MCP_HOST}:443", "/mcp", json={})

    assert response.status_code == 200
    assert mcp_upstream.last.path == "/mcp"


def test_access_log_records_what_the_caller_sent(gateway, specialist_upstream):
    """The path and host the caller asked for (not the upstream rewrite), even when it forges them."""
    traceparent = "00-6e0c63257de34c926f9efcd03899b72d-0f1c2b3a4d5e6f70-01"
    forged = {"x-envoy-original-path": "/forged", "x-envoy-original-host": "forged.example"}

    gateway.get(SPECIALIST_HOST, "/v1/card", headers={"traceparent": traceparent, **forged})

    entry = gateway.envoy.access_log_entry(traceparent)
    assert (entry["path"], entry["authority"]) == ("/v1/card", SPECIALIST_HOST)
    assert entry["upstream_cluster"] == "ai-coe-mortgage-specialist"
    assert specialist_upstream.last.headers.get("x-envoy-original-path") != "/forged"


def test_responses_do_not_reveal_upstream_timing(gateway, mcp_upstream):
    assert "x-envoy-upstream-service-time" not in gateway.post(MCP_HOST, "/mcp", json={}).headers


def test_dot_segments_cannot_leave_the_a2a_base_path(gateway, specialist_upstream):
    """The gateway's access token only goes below the engine's /a2a path, whatever the caller sends."""
    assert gateway.raw("GET", SPECIALIST_HOST, "/../../456:streamQuery").status == 200
    assert specialist_upstream.last.path == "/v1beta1/projects/p/locations/l/reasoningEngines/123/a2a/456:streamQuery"


@pytest.mark.parametrize("path", ["/v1/card", "/v1/message:send", "/v1/tasks/task-1"])
def test_specialist_route_prefixes_engine_a2a_path(gateway, specialist_upstream, path):
    gateway.post(SPECIALIST_HOST, path, json={})

    assert specialist_upstream.last.path == "/v1beta1/projects/p/locations/l/reasoningEngines/123/a2a" + path
    assert specialist_upstream.last.headers["host"] == "localhost"


APEX_HOST = "esmeralda.internal"


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/legacy-dms/mcp", "/mcp"),
        ("/agents/legacy-dms/mcp", "/mcp"),
        ("/legacy-dms", "/"),
        ("/agents/legacy-dms", "/"),
    ],
)
def test_apex_routes_strip_the_target_prefix(gateway, mcp_upstream, path, expected):
    response = gateway.post(APEX_HOST, path, json={})

    assert response.status_code == 200
    assert mcp_upstream.last.path == expected


def test_apex_specialist_route_keeps_a2a_prefix(gateway, specialist_upstream):
    gateway.get(APEX_HOST, "/ai-coe-mortgage-specialist/v1/card")

    assert specialist_upstream.last.path == "/v1beta1/projects/p/locations/l/reasoningEngines/123/a2a/v1/card"


def test_unknown_host_returns_404(gateway):
    assert gateway.get("nope.esmeralda.internal", "/mcp").status_code == 404


def test_unknown_apex_path_returns_404(gateway):
    assert gateway.get(APEX_HOST, "/nope/mcp").status_code == 404


def test_healthz_on_apex_returns_200_without_calling_upstreams(gateway, upstreams):
    for upstream in upstreams.values():
        upstream.reset()

    response = gateway.get(APEX_HOST, "/healthz")

    assert response.status_code == 200
    assert all(not upstream.requests for upstream in upstreams.values())


ORCHESTRATOR_HOST = "cx-mortgage-orchestrator.esmeralda.internal"
ORCHESTRATOR_STREAM_QUERY = "/v1beta1/projects/p/locations/l/reasoningEngines/456:streamQuery"


@pytest.mark.parametrize(
    "host, path",
    [
        (ORCHESTRATOR_HOST, "/api/stream_reasoning_engine"),
        (APEX_HOST, "/cx-mortgage-orchestrator/api/stream_reasoning_engine"),
        (APEX_HOST, "/agents/cx-mortgage-orchestrator/api/stream_reasoning_engine"),
    ],
)
def test_orchestrator_stream_path_maps_to_stream_query_sse(gateway, orchestrator_upstream, host, path):
    """Same path esmeralda serve exposes locally; Agent Runtime serves it as :streamQuery?alt=sse."""
    response = gateway.post(host, path, json={"class_method": "async_stream_query", "input": {}})

    assert response.status_code == 200
    assert orchestrator_upstream.last.path == ORCHESTRATOR_STREAM_QUERY
    assert orchestrator_upstream.last.query == {"alt": ["sse"]}


def test_orchestrator_rejects_unmapped_paths(gateway, orchestrator_upstream):
    assert gateway.post(ORCHESTRATOR_HOST, "/", json={}).status_code == 404
    assert gateway.post(ORCHESTRATOR_HOST, "/api/reasoning_engine", json={}).status_code == 404
    assert orchestrator_upstream.requests == []


def test_orchestrator_rejects_caller_query_strings(gateway, orchestrator_upstream):
    """Mapped paths bring their own ?alt=sse; Envoy would append the caller's query after it (?alt=sse?x)."""
    for host, path in [
        (ORCHESTRATOR_HOST, "/api/stream_reasoning_engine"),
        (APEX_HOST, "/cx-mortgage-orchestrator/api/stream_reasoning_engine"),
    ]:
        assert gateway.post(host, path, params={"alt": "json"}, json={}).status_code == 400, (host, path)
    assert orchestrator_upstream.requests == []
