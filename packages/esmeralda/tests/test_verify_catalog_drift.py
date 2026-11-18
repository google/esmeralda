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

"""The ``catalog-drift`` step of ``esmeralda verify``: warning only unless strict, unreachable
sources are notices, and nothing to do when no external source is enabled.

Synthetic only: a fake snapshot adapter (``fixture_inventory``), the fixture snapshot under
``tests/fixtures/catalog_sources/``, no network.
"""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from esmeralda import cli
from esmeralda.catalog import SnapshotSource, SourceUnreachable, commands, register_source
from esmeralda.catalog import sources as catalog_sources
from esmeralda.catalog.snapshot import Placeholders
from esmeralda.ops import registry_egress, runtime

REPO = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "catalog_sources"
SNAPSHOT = FIXTURES / "catalog" / "snapshots" / "inventory" / "dev.json"
SOURCE_ENTRY = "  - name: inventory\n    type: fixture_inventory\n"


@pytest.fixture
def live(monkeypatch) -> dict[str, Any]:
    """A snapshot adapter whose live entries equal the fixture snapshot; mutate the state to drift."""
    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))
    entries = json.loads(SNAPSHOT.read_text())["entries"]
    state: dict[str, Any] = {"entries": copy.deepcopy(entries), "unreachable": False, "boom": False}

    @register_source("fixture_inventory")
    class DriftInventory(SnapshotSource):
        CAPABILITIES = {"inventory": ["*"],
                        "targets": ["deploy.endpoint", "runtime", "runtime_resource", "description", "registry"]}

        def placeholders(self) -> Placeholders:
            return Placeholders()

        def fetch(self) -> list[dict[str, Any]]:
            if state["boom"]:
                raise RuntimeError("adapter bug (synthetic)")
            if state["unreachable"]:
                raise SourceUnreachable(f"{self.name}: the live source can't be reached (synthetic)")
            return copy.deepcopy(state["entries"])

    return state


def _base_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO / "catalog", root / "catalog")
    (root / "contracts").mkdir()
    for name in ("catalog.schema.json", "catalog-snapshot.schema.json"):
        shutil.copy(REPO / "contracts" / name, root / "contracts")
    (root / "infrastructure/live").mkdir(parents=True)
    (root / "infrastructure/live/root.hcl").write_text("")
    return root


@pytest.fixture
def repo(tmp_path: Path, live) -> Path:
    """The repository catalog plus the fixture snapshot source (one discovered entry)."""
    root = _base_repo(tmp_path)
    shutil.copytree(FIXTURES / "catalog", root / "catalog", dirs_exist_ok=True)
    path = root / "catalog/sources.yaml"
    path.write_text(path.read_text().replace("\n\nenvironments_dir:", f"\n{SOURCE_ENTRY}\nenvironments_dir:"))
    return root


def _set_policy(root: Path, mode: str) -> None:
    path = root / "catalog/policy.yaml"
    path.write_text(path.read_text().replace("catalog_drift: warn", f"catalog_drift: {mode}"))


def test_no_external_source_enabled_passes_without_resolving(tmp_path, monkeypatch):
    root = _base_repo(tmp_path)

    def no_resolve(*_a: Any, **_k: Any) -> None:
        raise AssertionError("resolved the catalog with no external source enabled")

    monkeypatch.setattr(commands, "resolve", no_resolve)
    result = runtime.verify_catalog_drift(root, "dev", strict=True)
    assert result["sources"] == [] and result["findings"] == []
    assert result["step"] == {"step": "catalog-drift", "passed": True, "warning": False,
                              "seconds": result["step"]["seconds"], "detail": "no external catalog sources enabled"}


def test_findings_warn_by_default_with_counts_top_findings_and_the_command(repo):
    result = runtime.verify_catalog_drift(repo, "dev")
    step = result["step"]
    assert step["passed"] is True and step["warning"] is True
    assert result["mode"] == "warn" and result["sources"] == ["inventory"]
    assert result["counts"] == {"discovered": 1} and result["live_checked"] == ["inventory"]
    assert "1 finding(s): 1 discovered; [discovered] inventory.loan-helper" in step["detail"]
    assert "esmeralda catalog diff --env dev --live" in step["detail"]


def test_strict_flag_or_policy_fail_makes_findings_fail(repo):
    assert runtime.verify_catalog_drift(repo, "dev", strict=True)["step"]["passed"] is False
    _set_policy(repo, "fail")
    result = runtime.verify_catalog_drift(repo, "dev")
    assert result["mode"] == "fail" and result["step"]["passed"] is False


def test_live_drift_is_reported(repo, live):
    live["entries"] = [e for e in live["entries"] if e["id"] != "agents/loan-helper"]
    result = runtime.verify_catalog_drift(repo, "dev")
    assert result["counts"] == {"discovered": 1, "live_drift": 1}
    assert result["step"]["passed"] is True


@pytest.mark.parametrize(("flag", "expected"), [("unreachable", "inventory: unreachable"),
                                                ("boom", "inventory: live check failed: RuntimeError")])
def test_an_unreachable_or_broken_source_is_a_notice_never_a_failure(repo, live, flag, expected):
    live[flag] = True
    result = runtime.verify_catalog_drift(repo, "dev", strict=True)
    assert result["live_checked"] == [] and expected in result["notices"][0]
    assert result["counts"] == {"discovered": 1}, "offline findings are still reported"
    assert expected in result["step"]["detail"]
    assert result["step"]["passed"] is False, "strict fails on the offline finding, not on the notice"


def test_unreachable_source_alone_passes_even_when_strict(repo, live, monkeypatch):
    monkeypatch.setattr(commands, "_offline_findings", lambda _resolved: [])
    live["unreachable"] = True
    step = runtime.verify_catalog_drift(repo, "dev", strict=True)["step"]
    assert step["passed"] is True and step["warning"] is True and "unreachable" in step["detail"]


def test_a_catalog_that_does_not_resolve_warns_and_fails_only_when_strict(repo):
    (repo / "catalog/snapshots/inventory/dev.json").unlink()  # a required snapshot is missing
    result = runtime.verify_catalog_drift(repo, "dev")
    assert result["step"]["passed"] is True and "catalog does not resolve" in result["step"]["detail"]
    assert "catalog fetch --env dev --source inventory" in result["step"]["detail"]
    assert runtime.verify_catalog_drift(repo, "dev", strict=True)["step"]["passed"] is False


def test_policy_rejects_unknown_modes(repo):
    _set_policy(repo, "maybe")
    result = runtime.verify_catalog_drift(repo, "dev")
    assert "catalog does not resolve" in result["step"]["detail"] and "policy" in result["step"]["detail"]


def _fake_gateway(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "test_gateway", lambda *a, **k: {"passed": True, "ok": 1, "failed": 0, "hosts": []})
    monkeypatch.setattr(registry_egress, "check",
                        lambda root, env, **_: {"env": env, "passed": True, "ok": 1, "failed": 0, "checks": []})


def test_verify_includes_the_step_and_skip_catalog_or_probe_drop_it(repo, monkeypatch):
    _fake_gateway(monkeypatch)
    result = asyncio.run(runtime.verify(repo, "dev", skip_query=True))
    assert [s["step"] for s in result["steps"]] == ["gateway", "registry-egress", "catalog-drift"]
    assert result["passed"] is True and result["catalog_drift"]["counts"] == {"discovered": 1}
    assert "step" not in result["catalog_drift"]

    strict = asyncio.run(runtime.verify(repo, "dev", skip_query=True, catalog_strict=True))
    assert strict["passed"] is False

    skipped = asyncio.run(runtime.verify(repo, "dev", skip_query=True, skip_catalog=True, catalog_strict=True))
    assert [s["step"] for s in skipped["steps"]] == ["gateway", "registry-egress"] and skipped["passed"] is True
    assert "catalog_drift" not in skipped
    probed = asyncio.run(runtime.verify(repo, "dev", probe="legacy-dms", skip_query=True))
    assert [s["step"] for s in probed["steps"]] == ["gateway"]


def test_cli_verify_flags_json_and_warning_icon(repo, monkeypatch, capsys):
    _fake_gateway(monkeypatch)
    args = ["verify", "--env", "dev", "--skip-query", "--repo", str(repo)]
    assert cli.main([*args, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["steps"][-1]["step"] == "catalog-drift" and data["catalog_drift"]["mode"] == "warn"

    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "⚠️  catalog-drift" in out and "Verification passed" in out

    assert cli.main([*args, "--catalog-strict"]) == 1
    assert "❌ catalog-drift" in capsys.readouterr().out
    assert cli.main([*args, "--catalog-strict", "--skip-catalog", "--json"]) == 0
    assert "catalog_drift" not in json.loads(capsys.readouterr().out)
