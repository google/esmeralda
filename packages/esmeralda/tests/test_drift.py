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

"""Tests for read-only drift checks of script-managed settings."""

from __future__ import annotations

from pathlib import Path

from esmeralda.ops import drift, registry_egress


def test_check_reports_only_failed_registry_checks(monkeypatch):
    payload = {
        "env": "dev",
        "passed": False,
        "ok": 2,
        "failed": 1,
        "checks": [
            {"check": "permit-iap-egress", "ok": True, "detail": "matches"},
            {"check": "external-probe", "ok": False, "detail": "manual member added"},
        ],
        "seconds": 0.25,
    }
    seen: list[tuple[Path, str]] = []

    def fake_check(root: Path, env: str) -> dict:
        seen.append((root, env))
        return payload

    monkeypatch.setattr(registry_egress, "check", fake_check)
    result = drift.check(Path("/tmp/repo"), "dev")

    assert seen == [(Path("/tmp/repo"), "dev")]
    assert result["passed"] is False
    assert result["sources"] == ["iap-egress"]
    assert result["ok_count"] == 2
    assert result["failed"] == 1
    assert result["seconds"] == 0.25
    assert result["checks"] == [
        {"check": "external-probe", "ok": False, "detail": "manual member added", "source": "iap-egress"}
    ]


def test_check_passes_when_registry_is_clean(monkeypatch):
    monkeypatch.setattr(
        registry_egress,
        "check",
        lambda root, env: {"env": env, "passed": True, "ok": 3, "failed": 0, "checks": [], "seconds": 0.1},
    )

    result = drift.check(Path("."), "prd")

    assert result["passed"] is True
    assert result["failed"] == 0
    assert result["checks"] == []
