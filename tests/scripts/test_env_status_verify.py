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
"""Environment status (scripts/env_status.py) and verify helpers, on a synthetic local-state tree."""

import json

import env_status
import pytest
import verify_env

DIGEST = "us-docker.pkg.dev/demo/repo/svc@sha256:" + "ab" * 32


@pytest.fixture
def live(tmp_path, monkeypatch):
    live = tmp_path / "infrastructure" / "live"
    (live / "demo").mkdir(parents=True)
    (live / "demo" / "env.yaml").write_text('locals {\n  state_project = ""\n  state_bucket  = ""\n}\n')
    for unit in ("layer-2-networking", "layer-5-workloads/services/svc", "layer-5-workloads/gateways/gw"):
        (live / "demo" / unit).mkdir(parents=True)
        (live / "demo" / unit / "terragrunt.hcl").write_text("")
    states = live / ".local_states" / "demo"

    def state(unit, outputs, resources=()):
        (states / unit).mkdir(parents=True)
        (states / unit / "terraform.tfstate").write_text(json.dumps({"outputs": outputs, "resources": list(resources)}))

    state(
        "layer-2-networking",
        {
            "entrypoint_address": {"value": "10.0.0.5"},
            "subnet_id": {"value": "s"},
            "secret": {"value": "x", "sensitive": True},
        },
    )
    state(
        "layer-5-workloads/services/svc",
        {"service_uri": {"value": "https://svc.example"}},
        [{"mode": "managed", "instances": [{"attributes": {"template": [{"containers": [{"image": DIGEST}]}]}}]}],
    )
    monkeypatch.setattr(env_status, "LIVE_DIR", live)
    return live


def test_collect_reads_local_state(live):
    data = env_status.collect("demo")
    assert data["backend"] == "local"
    assert data["not_deployed"] == ["layer-5-workloads/gateways/gw"]
    net = data["units"]["layer-2-networking"]["outputs"]
    assert net["entrypoint_address"] == "10.0.0.5" and net["secret"] == "<sensitive>"
    assert data["units"]["layer-5-workloads/services/svc"]["images"] == [DIGEST]


def test_table_shows_key_outputs_only(live):
    table = env_status.render_table(env_status.collect("demo"))
    assert "entrypoint_address  10.0.0.5" in table
    assert "subnet_id" not in table and "secret" not in table
    assert "svc@sha256:abababababab" in table
    assert "not deployed: layer-5-workloads/gateways/gw" in table
    assert "subnet_id" in env_status.render_table(env_status.collect("demo"), show_all=True)


def test_compact_routes_and_truncation():
    routes = {"a": {"host": "a.internal", "probe": "mcp"}, "b": {"host": "b.internal", "probe": "a2a"}}
    assert env_status.compact("routes", routes) == "a(mcp), b(a2a)"
    assert env_status.compact("x", "y" * 500).endswith("…")


def test_gcs_state_uri():
    assert env_status.gcs_state_uri("bkt", "dev", "layer-3-security") == (
        "gs://bkt/dev/layer-3-security/terraform.tfstate/default.tfstate"
    )


def test_gcs_backend_detected(live):
    (live / "demo" / "env.yaml").write_text('locals {\n  state_project = "p"\n  state_bucket = "b"\n}\n')
    assert env_status.env_locals("demo")["state_bucket"] == "b"


# --- verify ----------------------------------------------------------------------------------------


def test_extract_answer():
    out = (
        "🧪 agent\n  target x\n\n\x1b[1m🤖 Answer (from orchestrator)\x1b[0m\n"
        "  Income verified.\n\n  Second line.\n\n📋 Result\n  events 3\n"
    )
    assert verify_env.extract_answer(out) == "Income verified.\nSecond line."
    assert verify_env.extract_answer("🤖 Answer\n  (no text answer)\n\n📋 Result\n") == ""
    assert verify_env.extract_answer("no answer section") == ""


def test_summary_table():
    results = [
        verify_env.Result("gateway", True, 12.2, None),
        verify_env.Result("orchestrator", False, 40.0, None, "exit 2"),
    ]
    rows = [line.split() for line in verify_env.summary_table(results).splitlines()]
    assert rows[0] == ["STEP", "RESULT", "TIME"]
    assert rows[2] == ["gateway", "PASS", "12s"]
    assert rows[3] == ["orchestrator", "FAIL", "40s", "exit", "2"]
