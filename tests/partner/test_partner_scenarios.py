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

"""Offline checks of the partner scenario catalog (tests/partner/scenarios.py)."""

from __future__ import annotations

import ast
import json
import re
import xml.etree.ElementTree as ET

import pytest
from partner_testlib import PARTNER_DIR, REPO_ROOT, load

sc = load("scenarios", PARTNER_DIR / "scenarios.py")
PROXIES_DIR = REPO_ROOT / "apps" / "apigee" / "proxies"
TEST_PARTNER_SH = REPO_ROOT / "scripts" / "test_partner.sh"
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def test_names_are_unique_kebab_case() -> None:
    names = [s.name for s in sc.SCENARIOS]
    assert len(names) == len(set(names)), names
    assert all(NAME.match(n) for n in names), names


@pytest.mark.parametrize("scenario", sc.SCENARIOS, ids=lambda s: s.name)
def test_scenario_schema(scenario) -> None:
    assert scenario.description and scenario.proxy
    assert scenario.method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
    assert scenario.path.startswith("/partner/v1/"), scenario.path
    assert scenario.expect and all(100 <= c <= 599 for c in scenario.expect)
    assert scenario.auth in sc.AUTH_MODES
    assert scenario.weight >= 0 and scenario.attempts >= 1 and scenario.timeout > 0
    assert (scenario.body is None) == (scenario.method == "GET"), "GET has no body, others send JSON"
    if scenario.body is not None:
        json.dumps(scenario.body)
    assert "x-api-key" not in {h.lower() for h in scenario.headers}, "keys come from auth, never headers"
    if scenario.stream:
        assert scenario.headers.get("Accept") == "text/event-stream"
        assert scenario.timeout <= 300, "the Apigee SSE target times out at 300 s"
    if scenario.auth != sc.AUTH_KEY:
        assert scenario.expect == frozenset({401}), "a missing or unknown key must be rejected with 401"


def test_proxy_base_paths_match_bundles() -> None:
    """Each scenario path starts with its proxy's BasePath when the bundle lives in this repo."""
    checked = 0
    for scenario in sc.SCENARIOS:
        endpoint = PROXIES_DIR / scenario.proxy / "apiproxy" / "proxies" / "default.xml"
        if not endpoint.is_file():
            continue  # e.g. a proxy added by another capability bundle
        base = ET.parse(endpoint).getroot().findtext("HTTPProxyConnection/BasePath")
        assert base and scenario.path.startswith(base + "/"), (scenario.name, base)
        checked += 1
    assert checked >= 5


def test_conformance_probes_are_numbered_and_labelled() -> None:
    nums = [s.probe for s in sc.probes()]
    assert nums == list(range(1, len(nums) + 1)), nums
    assert all(s.label for s in sc.probes())


def test_todays_five_probes() -> None:
    """The 5 probes of `esmeralda verify --partner` keep their meaning."""
    p = {s.probe: s for s in sc.probes()}
    assert p[1].auth == sc.AUTH_NONE and p[1].expect == frozenset({401})
    assert "ai-coe-mortgage-specialist" in p[2].path and p[2].expect == frozenset({403, 404})
    assert p[3].proxy == "partner-mcp" and p[3].body["method"] == "tools/call" and p[3].expect == frozenset({403})
    assert p[4].stream and p[4].expect == frozenset({200}) and p[4].contains == "Sterling"
    assert p[4].path.endswith("cx-mortgage-orchestrator:streamQuery?alt=sse")
    assert p[5].destructive and p[5].expect == frozenset({429}) and p[5].attempts > 1


def test_quota_exhaustion_is_opt_in() -> None:
    destructive = [s for s in sc.SCENARIOS if s.destructive]
    assert destructive, "the quota exhaustion scenario must stay in the catalog"
    assert all(429 in s.expect for s in destructive)
    default_mix = sc.load_mix()
    assert not any(s.destructive for s in default_mix)
    assert {s.name for s in destructive} <= {s.name for s in sc.load_mix(include_destructive=True)}


def test_default_mix_exercises_both_proxies_and_real_answers() -> None:
    mix = sc.load_mix()
    assert {s.proxy for s in mix} >= {"partner-agents", "partner-mcp"}
    assert sum(1 for s in mix if s.stream and 200 in s.expect) >= 3, "several real orchestrator questions"
    assert any(s.auth == sc.AUTH_INVALID for s in mix)


def test_render_fills_placeholders_without_mutating_the_catalog() -> None:
    s = sc.by_name("orchestrator-income")
    path, headers, body = s.render("partner-user-1", "req-1")
    assert path == s.path and headers["Accept"] == "text/event-stream"
    assert json.loads(body)["input"]["user_id"] == "partner-user-1"
    assert s.body["input"]["user_id"] == "{user_id}"


def test_auth_headers() -> None:
    assert sc.by_name("orchestrator-income").auth_headers("k") == {"x-api-key": "k"}
    assert sc.by_name("agents-no-key").auth_headers("k") == {}
    assert sc.by_name("agents-invalid-key").auth_headers("k") == {"x-api-key": sc.INVALID_API_KEY}


def test_check() -> None:
    s = sc.by_name("orchestrator-income")
    assert s.check(200, "data: Julian STERLING verified") is None
    assert "without 'Sterling'" in s.check(200, "data: hello")
    assert "expected 200" in s.check(504, "")
    assert sc.by_name("agents-specialist-card-blocked").check(404, "") is None


def test_catalog_is_stdlib_only() -> None:
    """scenarios.py and probe.py run on the test VM's bare python3."""
    for path in (PARTNER_DIR / "scenarios.py", PARTNER_DIR / "probe.py"):
        tree = ast.parse(path.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported <= {
            "__future__", "copy", "dataclasses", "http", "json", "os", "socket", "ssl", "sys", "time",
            "typing", "uuid", "scenarios",
        }, (path.name, imported)


def test_test_partner_sh_runs_the_catalog() -> None:
    """The live probes come from the catalog: the script ships probe.py + scenarios.py to the VM."""
    script = TEST_PARTNER_SH.read_text()
    assert "scenarios.py" in script and "probe.py" in script
    remote = script.split("REMOTE_EOF", 1)[1]
    assert "curl " not in remote, "live probes belong in tests/partner/scenarios.py, not in curl calls"
