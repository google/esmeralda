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

"""Long calls and streams (H2, H4; UC5, UC15, UC16). These tests take tens of seconds by design."""

import time

SPECIALIST_HOST = "ai-coe-mortgage-specialist.esmeralda.internal"


def test_blocking_call_longer_than_envoy_default_timeout_succeeds(gateway, specialist_upstream):
    """A2A message:send blocks while the agent works (client timeout 60 s); Envoy's default route timeout is 15 s."""
    specialist_upstream.delay_s = 16

    response = gateway.post(SPECIALIST_HOST, "/v1/message:send", json={"message": {}}, timeout=30)

    assert response.status_code == 200


ORCHESTRATOR_HOST = "cx-mortgage-orchestrator.esmeralda.internal"


def test_sse_events_arrive_unbuffered_past_15_seconds(gateway, orchestrator_upstream):
    """Events reach the caller as they are produced, and a 16 s pause between two of them (longer than
    Envoy's 15 s default route timeout) doesn't end the stream."""
    orchestrator_upstream.sse_events, orchestrator_upstream.sse_gap_s = 2, 16
    started = time.monotonic()
    arrivals = []

    with gateway.stream("POST", ORCHESTRATOR_HOST, "/api/stream_reasoning_engine", json={}, timeout=40) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data:"):
                arrivals.append(time.monotonic() - started)

    assert len(arrivals) == 2
    assert arrivals[0] < 3, f"first event held back: {arrivals}"
    assert arrivals[1] - arrivals[0] > 15, f"stream ended early or events were batched: {arrivals}"
