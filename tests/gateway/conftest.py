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

"""Fixtures for the Envoy gateway harness. Helpers live in ``harness.py``."""

from __future__ import annotations

import dataclasses

import pytest
from harness import GOOGLE_AUDIENCE, RenderedConfig, free_port, render_config

# Targets the fixture gateway serves, mirroring the real ones (plus two TLS failure cases).
UPSTREAMS = (
    "legacy-dms",
    "corporate-email",
    "ai-coe-mortgage-specialist",
    "cx-mortgage-orchestrator",
    "untrusted",
    "mismatched",
)
SPECIALIST_A2A_PATH = "/v1beta1/projects/p/locations/l/reasoningEngines/123/a2a"
ORCHESTRATOR_ENGINE_PATH = "/v1beta1/projects/p/locations/l/reasoningEngines/456"


@dataclasses.dataclass
class Ports:
    listener: int
    admin: int
    metadata: int
    upstreams: dict[str, int]


@pytest.fixture(scope="session")
def ports() -> Ports:
    return Ports(
        listener=free_port(),
        admin=free_port(),
        metadata=free_port(),
        upstreams={name: free_port() for name in UPSTREAMS},
    )


@pytest.fixture(scope="session")
def endpoints(ports) -> dict[str, dict]:
    """``agent_endpoints`` as the live stack passes them; upstreams are local mocks on ``localhost``."""

    def endpoint(name: str, base_path: str = "", audience: str | None = None, **extra) -> dict:
        origin = f"https://localhost:{ports.upstreams[name]}"
        return {
            "logical_name": name,
            "engine_id": f"{name}-id",
            "endpoint_url": origin + base_path,
            "audience": origin if audience is None else audience,
            **extra,
        }

    return {
        "legacy-dms": endpoint("legacy-dms"),
        "corporate-email": endpoint("corporate-email"),
        "ai-coe-mortgage-specialist": endpoint("ai-coe-mortgage-specialist", SPECIALIST_A2A_PATH, GOOGLE_AUDIENCE),
        "cx-mortgage-orchestrator": endpoint(
            "cx-mortgage-orchestrator",
            ORCHESTRATOR_ENGINE_PATH,
            GOOGLE_AUDIENCE,
            path_map={"/api/stream_reasoning_engine": ":streamQuery?alt=sse"},
            streaming=True,
        ),
        "untrusted": endpoint("untrusted"),
        "mismatched": endpoint("mismatched"),
    }


@pytest.fixture(scope="session")
def standard_config(tmp_path_factory, ports, endpoints) -> RenderedConfig:
    return render_config(
        tmp_path_factory.mktemp("standard"),
        agent_endpoints=endpoints,
        listener_port=ports.listener,
        admin_port=ports.admin,
        metadata_host="127.0.0.1",
        metadata_port=ports.metadata,
    )
