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

"""Fixtures for the Envoy gateway harness. Helpers live in ``harness.py``, fakes in ``fakes.py``."""

from __future__ import annotations

import contextlib
import dataclasses
import types

import pytest
from fakes import FakeMetadataServer, MockUpstream, make_ca, make_leaf
from harness import GOOGLE_AUDIENCE, EnvoyProcess, Gateway, RenderedConfig, free_port, render_config

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
def pki(tmp_path_factory) -> types.SimpleNamespace:
    """A trusted CA (the bundle Envoy trusts) and an unknown CA, for the TLS failure cases."""
    directory = tmp_path_factory.mktemp("pki")
    trusted, unknown = make_ca("Gateway Harness Trusted CA"), make_ca("Gateway Harness Unknown CA")
    ca_bundle = directory / "ca-bundle.pem"
    ca_bundle.write_bytes(trusted.pem)
    return types.SimpleNamespace(directory=directory, trusted=trusted, unknown=unknown, ca_bundle=ca_bundle)


@pytest.fixture(scope="session")
def standard_config(tmp_path_factory, ports, endpoints, pki) -> RenderedConfig:
    return render_config(
        tmp_path_factory.mktemp("standard"),
        agent_endpoints=endpoints,
        listener_port=ports.listener,
        admin_port=ports.admin,
        metadata_host="127.0.0.1",
        metadata_port=ports.metadata,
        ca_bundle_path=str(pki.ca_bundle),
    )


@pytest.fixture(scope="session")
def metadata(ports):
    with FakeMetadataServer(ports.metadata) as server:
        yield server


@pytest.fixture(scope="session")
def upstreams(ports, pki):
    """One TLS mock per target: ``untrusted`` uses the unknown CA, ``mismatched`` a certificate for another host."""
    certificates = {"untrusted": (pki.unknown, "localhost"), "mismatched": (pki.trusted, "wrong.example")}
    with contextlib.ExitStack() as stack:
        servers = {}
        for name in UPSTREAMS:
            ca, san = certificates.get(name, (pki.trusted, "localhost"))
            cert_file, key_file = make_leaf(ca, san, pki.directory)
            servers[name] = stack.enter_context(MockUpstream(ports.upstreams[name], cert_file, key_file))
        yield servers


@pytest.fixture(scope="session")
def gateway(tmp_path_factory, standard_config, metadata, upstreams):
    with EnvoyProcess(standard_config, tmp_path_factory.mktemp("envoy") / "envoy.log") as envoy:
        client = Gateway(envoy)
        yield client
        client.close()


def _fresh(upstream: MockUpstream):
    """Resets the upstream before and after the test, so a delay or SSE mode never leaks into the next one."""
    upstream.reset()
    yield upstream
    upstream.reset()


@pytest.fixture
def mcp_upstream(upstreams):
    yield from _fresh(upstreams["legacy-dms"])


@pytest.fixture
def specialist_upstream(upstreams):
    yield from _fresh(upstreams["ai-coe-mortgage-specialist"])


@pytest.fixture
def orchestrator_upstream(upstreams):
    yield from _fresh(upstreams["cx-mortgage-orchestrator"])


def pytest_configure(config):
    """A SIGTERM aimed at pytest alone (CI cancellation, ``kill``) runs the teardowns, so no envoy outlives it."""
    import signal

    signal.signal(signal.SIGTERM, signal.default_int_handler)
