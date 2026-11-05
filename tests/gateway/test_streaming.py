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

"""Long calls and streams (H2, H4; UC15, UC16). These tests take tens of seconds by design."""

SPECIALIST_HOST = "ai-coe-mortgage-specialist.esmeralda.internal"


def test_blocking_call_longer_than_envoy_default_timeout_succeeds(gateway, specialist_upstream):
    """A2A message:send blocks while the agent works (client timeout 60 s); Envoy's default route timeout is 15 s."""
    specialist_upstream.delay_s = 16

    response = gateway.post(SPECIALIST_HOST, "/v1/message:send", json={"message": {}}, timeout=30)

    assert response.status_code == 200
