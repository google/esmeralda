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

"""H0: the rendered Envoy bootstrap is valid, keeps its admin interface private and lists its hosts."""

import subprocess

import pytest
from harness import APEX, proxied_routes, render_config, tool


def test_rendered_config_passes_envoy_validate(standard_config):
    result = subprocess.run(
        [tool("envoy"), "--mode", "validate", "-c", str(standard_config.path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_admin_listens_on_loopback_only(standard_config):
    assert standard_config.data["admin"]["address"]["socket_address"]["address"] == "127.0.0.1"


def test_hostnames_output_lists_apex_and_every_target(standard_config, endpoints):
    expected = [APEX] + [f"{name}.{APEX}" for name in endpoints]
    assert sorted(standard_config.hostnames) == sorted(expected)


def test_every_route_sets_an_explicit_timeout(standard_config, endpoints):
    """Envoy's default route timeout is 15 s; blocking A2A calls need more, SSE routes none at all."""
    for vhost, route in proxied_routes(standard_config):
        streaming = endpoints[route["route"]["cluster"]].get("streaming", False)
        assert route["route"].get("timeout") == ("0s" if streaming else "120s"), (vhost, route)


def test_every_route_retries_connect_failures_5_times(standard_config):
    """Kong services retry a failed upstream connection 5 times by default."""
    for vhost, route in proxied_routes(standard_config):
        policy = route["route"].get("retry_policy", {})
        assert "connect-failure" in policy.get("retry_on", "").split(","), (vhost, route)
        assert policy.get("num_retries") == 5, (vhost, route)


def _squash(text: str) -> str:
    """Terraform wraps long messages to the terminal width; compare them on one line."""
    return " ".join(text.split())


def test_malformed_endpoint_url_is_rejected_with_a_clear_error(tmp_path, endpoints):
    broken = {"legacy-dms": {**endpoints["legacy-dms"], "endpoint_url": "legacy-dms.a.run.app/mcp"}}

    with pytest.raises(AssertionError) as failure:
        render_config(tmp_path, agent_endpoints=broken)

    assert "endpoint_url must look like" in _squash(str(failure.value))


def test_gateway_tokens_never_travel_in_clear_text(tmp_path, endpoints):
    plain = {"legacy-dms": {**endpoints["legacy-dms"], "endpoint_url": "http://legacy-dms.internal"}}

    with pytest.raises(AssertionError) as failure:
        render_config(tmp_path, agent_endpoints=plain)

    assert "must use https:// when it has an audience" in _squash(str(failure.value))


def _cluster(config, name: str) -> dict:
    return next(cluster for cluster in config.data["static_resources"]["clusters"] if cluster["name"] == name)


def _port(cluster: dict) -> int:
    return cluster["load_assignment"]["endpoints"][0]["lb_endpoints"][0]["endpoint"]["address"]["socket_address"][
        "port_value"
    ]


def test_endpoint_ports_default_to_the_scheme(tmp_path, endpoints):
    """https:// means TLS on 443; plain http:// (only without an audience) means port 80 and no TLS."""
    base = endpoints["legacy-dms"]
    targets = {
        "secure": {**base, "logical_name": "secure", "endpoint_url": "https://secure.example/base"},
        "plain": {**base, "logical_name": "plain", "endpoint_url": "http://plain.example", "audience": ""},
    }

    config = render_config(tmp_path, agent_endpoints=targets)

    secure, plain = _cluster(config, "secure"), _cluster(config, "plain")
    assert (_port(secure), "transport_socket" in secure) == (443, True)
    assert (_port(plain), "transport_socket" in plain) == (80, False)


def test_target_without_audience_gets_no_gateway_token(tmp_path, endpoints):
    """Same rule as Kong had: no audience, no token (the caller's own Authorization goes through)."""
    config = render_config(tmp_path, agent_endpoints={"legacy-dms": {**endpoints["legacy-dms"], "audience": ""}})

    assert "metadata" not in _cluster(config, "legacy-dms")


def test_config_too_big_for_one_secret_version_is_rejected(tmp_path, endpoints):
    many = {f"t{i}": {**endpoints["legacy-dms"], "logical_name": f"t{i}"} for i in range(30)}

    with pytest.raises(AssertionError) as failure:
        render_config(tmp_path, agent_endpoints=many)

    assert "larger than 64 KiB" in _squash(str(failure.value))
