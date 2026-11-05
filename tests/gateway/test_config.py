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

from harness import APEX, proxied_routes, tool


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
