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

"""Policy-level Agent Gateway egress probe (esmeralda.ops.registry_egress), offline."""

from __future__ import annotations

import json

from esmeralda.ops import registry_egress, repo, runtime

SPEC = "principal://agents.global.org-1.system.id.goog/resources/aiplatform/projects/2/locations/r/reasoningEngines/spec"
ORCH = "principal://agents.global.org-1.system.id.goog/resources/aiplatform/projects/1/locations/r/reasoningEngines/orch"
AGENT_SET = "principalSet://agents.global.org-1.system.id.goog/attribute.platformContainer/aiplatform/projects/1"


def _service(sid: str, collection: str, uid: str, url: str | None = None, card_url: str | None = None) -> dict:
    service = {
        "name": f"projects/p/locations/r/services/{sid}",
        "registryResource": f"projects/0/locations/r/{collection}/{uid}",
    }
    if url:
        service["interfaces"] = [{"url": url, "protocolBinding": "HTTP_JSON"}]
    if card_url:
        service["agentSpec"] = {"content": {"supportedInterfaces": [{"url": card_url}]}}
    return service


def _policy(*members: str) -> dict:
    return {"bindings": [{"role": "roles/iap.egressor", "members": list(members)}]} if members else {"etag": "ACAB"}


SERVICES = [
    _service("telemetry", "endpoints", "u1", "https://telemetry.googleapis.com"),
    _service("external-probe", "endpoints", "u2", "https://external-probe.example.internal"),
    _service("legacy-dms", "mcpServers", "u3", "https://legacy-dms.example.internal/mcp"),
    _service("ai-coe-mortgage-specialist", "agents", "u4", card_url="https://ai-coe-mortgage-specialist.example.internal"),
]
GOOD = {
    "telemetry": _policy(AGENT_SET),
    "external-probe": _policy(SPEC),
    "legacy-dms": _policy(SPEC),
    "ai-coe-mortgage-specialist": _policy(ORCH),
}
TARGETS = {
    "agents": {"ai-coe-mortgage-specialist": [ORCH]},
    "endpoints": {"external-probe": [SPEC]},
    "mcp_servers": {"legacy-dms": [SPEC]},
}
EXTERNAL = {"external-probe": {"registry": "endpoint", "url": "https://external-probe.example.internal"}}


def _evaluate(**overrides):
    kwargs = {
        "services": SERVICES,
        "policies": GOOD,
        "root_policy": {"etag": "ACAB"},
        "system_ids": ["telemetry"],
        "system_members": [AGENT_SET],
        "target_members": TARGETS,
        "external_entries": EXTERNAL,
        **overrides,
    }
    return {c["check"]: c for c in registry_egress.evaluate(**kwargs)}


def test_everything_as_compiled_passes():
    checks = _evaluate()
    assert all(c["ok"] for c in checks.values()), checks
    assert set(checks) == {
        "registry root", "external external-probe", "entry telemetry", "entry external-probe",
        "entry legacy-dms", "entry ai-coe-mortgage-specialist",
    }


def test_a_root_binding_fails():
    root = {"version": 3, "bindings": [{"role": "roles/iap.egressor", "members": [AGENT_SET],
                                        "condition": {"expression": "true"}}]}
    assert not _evaluate(root_policy=root)["registry root"]["ok"]


def test_a_non_listed_caller_on_an_external_entry_fails():
    checks = _evaluate(policies={**GOOD, "external-probe": _policy(SPEC, ORCH)})
    assert not checks["entry external-probe"]["ok"]
    assert ORCH in checks["entry external-probe"]["detail"]


def test_a_listed_caller_missing_fails():
    assert not _evaluate(policies={**GOOD, "external-probe": _policy()})["entry external-probe"]["ok"]


def test_an_empty_compiled_list_expects_an_empty_policy():
    targets = {**TARGETS, "endpoints": {"external-probe": []}}
    assert _evaluate(target_members=targets, policies={**GOOD, "external-probe": _policy()})[
        "entry external-probe"]["ok"]
    assert not _evaluate(target_members=targets)["entry external-probe"]["ok"]


def test_external_entry_with_the_wrong_url_or_missing_fails():
    wrong = {"external-probe": {"registry": "endpoint", "url": "https://other.example.internal"}}
    assert not _evaluate(external_entries=wrong)["external external-probe"]["ok"]
    services = [s for s in SERVICES if not s["name"].endswith("/external-probe")]
    checks = _evaluate(services=services)
    assert not checks["external external-probe"]["ok"] and not checks["entry external-probe"]["ok"]


def test_unlisted_entry_must_have_no_policy():
    services = [*SERVICES, _service("stray", "endpoints", "u9", "https://stray.example.com")]
    assert _evaluate(services=services, policies={**GOOD, "stray": _policy()})["entry stray"]["ok"]
    assert not _evaluate(services=services, policies={**GOOD, "stray": _policy(AGENT_SET)})["entry stray"]["ok"]


def test_system_endpoint_drift_fails():
    assert not _evaluate(policies={**GOOD, "telemetry": _policy(AGENT_SET, SPEC)})["entry telemetry"]["ok"]


def test_agent_card_urls_are_read():
    assert registry_egress.entry_urls(
        {"agentSpec": {"content": json.dumps({"supportedInterfaces": [{"url": "https://a.example"}]})}}
    ) == ["https://a.example"]


def test_check_reads_outputs_and_queries_each_entry(monkeypatch, tmp_path):
    outputs = {
        ("layer-1-projects", "governance_project_id"): "example-governance",
        ("layer-4-governance", "system_endpoint_ids"): ["telemetry"],
        ("layer-4-governance", "iap_egress_members"): [AGENT_SET],
        ("layer-5-workloads/iap-egress", "target_iap_members"): TARGETS,
        ("layer-5-workloads/iap-egress", "external_registry_entries"): EXTERNAL,
    }
    monkeypatch.setattr(repo, "output", lambda root, env, unit, name: outputs.get((unit, name)))
    monkeypatch.setattr(repo, "env_locals", lambda root, env: 'region = "us-central1"\n')
    by_uid = {"u1": "telemetry", "u2": "external-probe", "u3": "legacy-dms", "u4": "ai-coe-mortgage-specialist"}
    calls = []

    def fake(root, args, what):
        calls.append(args)
        if args[:4] == ["alpha", "agent-registry", "services", "list"]:
            return SERVICES
        flag = [a for a in args if a.startswith(("--endpoint=", "--agent=", "--mcp-server="))]
        if not flag:
            return {"etag": "ACAB"}
        return GOOD[by_uid[flag[0].split("=", 1)[1]]]

    result = registry_egress.check(tmp_path, "dev", gcloud=fake)
    assert result["passed"] and result["failed"] == 0
    assert all("--project=example-governance" in a for a in calls)
    assert sum(1 for a in calls if any(x.startswith("--endpoint=") for x in a)) == 2


def test_verify_step_reports_a_failed_registry_check(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "test_gateway", lambda *a, **k: {"passed": True, "ok": 1, "failed": 0, "hosts": []})

    def boom(root, env, **_):
        raise repo.OpsError("No system_endpoint_ids output in layer-4-governance (dev): apply layer 4 governance first")

    monkeypatch.setattr(registry_egress, "check", boom)
    import asyncio

    result = asyncio.run(runtime.verify(tmp_path, "dev", skip_query=True))
    steps = {s["step"]: s for s in result["steps"]}
    assert not result["passed"] and not steps["registry-egress"]["passed"]
    assert "apply layer 4" in steps["registry-egress"]["detail"]
    # --probe narrows verify to gateway routes: no registry step.
    probed = asyncio.run(runtime.verify(tmp_path, "dev", probe="legacy-dms", skip_query=True))
    assert [s["step"] for s in probed["steps"]] == ["gateway"]
