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

"""The harness's own helpers: tool versions and local ports."""

import pytest
from harness import MIN_ENVOY_VERSION, envoy_version, free_port


@pytest.mark.parametrize(
    "banner, expected",
    [
        ("envoy  version: 1d2c3b4a/1.40.2/Distribution/RELEASE/BoringSSL", (1, 40, 2)),
        ("envoy  version: 0a1b2c3d/1.38.0-dev/Modified/DEBUG/BoringSSL", (1, 38, 0)),
    ],
)
def test_envoy_version_is_read_from_the_banner(banner, expected):
    assert envoy_version(banner) == expected


def test_envoy_version_rejects_an_unrecognized_banner():
    with pytest.raises(ValueError, match="no version"):
        envoy_version("not envoy")


def test_harness_requires_an_envoy_with_gcp_authn_access_tokens():
    """Audience.access_token (the Vertex AI routes) needs Envoy 1.39 or later."""
    assert MIN_ENVOY_VERSION == (1, 39, 0)


def test_free_port_never_hands_out_the_same_port_twice():
    ports = [free_port() for _ in range(50)]
    assert len(set(ports)) == len(ports)
