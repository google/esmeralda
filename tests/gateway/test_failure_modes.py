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

"""Failure modes (H7, H10, H11; UC10, UC14, UC17): a token outage behaves like Kong did; untrusted upstreams
are refused after the connection retries."""

from fakes import bearer

MCP_HOST = "legacy-dms.esmeralda.internal"


def test_metadata_outage_forwards_caller_token_unchanged(outage_gateway, mcp_upstream):
    """Same as Kong's plugin: without a gateway token, the request keeps the caller's own Authorization."""
    response = outage_gateway.post(MCP_HOST, "/mcp", json={}, headers={"Authorization": "Bearer caller-id-token"})

    assert response.status_code == 200
    assert bearer(mcp_upstream.last) == "caller-id-token"
    assert outage_gateway.envoy.counter("http.ingress.gcp_authn.token_fetch_failed") >= 1


def _refused_by_tls(gateway, upstreams, name: str, verify_stat: str) -> None:
    """A 503 alone could be a dead port: also check that TLS verification failed, and that the connection
    was retried exactly 5 times (H10) before Envoy gave up."""
    upstreams[name].reset()
    verify_failures = gateway.envoy.counter(f"cluster.{name}.ssl.{verify_stat}")
    retries = gateway.envoy.counter(f"cluster.{name}.upstream_rq_retry")

    response = gateway.get(f"{name}.esmeralda.internal", "/mcp")

    assert response.status_code == 503
    assert upstreams[name].requests == []
    assert gateway.envoy.counter(f"cluster.{name}.ssl.{verify_stat}") > verify_failures
    assert gateway.envoy.counter(f"cluster.{name}.upstream_rq_retry") == retries + 5


def test_upstream_signed_by_unknown_ca_is_rejected(gateway, upstreams):
    _refused_by_tls(gateway, upstreams, "untrusted", "fail_verify_error")


def test_upstream_cert_for_another_hostname_is_rejected(gateway, upstreams):
    """The certificate chains to the trusted CA but names wrong.example, not the target host."""
    _refused_by_tls(gateway, upstreams, "mismatched", "fail_verify_san")
