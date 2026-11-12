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

"""Verified attribution guards: vendored caller guard copies and the unregistered probe identity."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CANONICAL = REPO / "packages/esmeralda/src/esmeralda/attribution.py"
SERVICES = ("corporate-email", "income-verification", "legacy-dms")
SERVICE_APPS = {"corporate-email": "main.py", "income-verification": "main.py", "legacy-dms": "server.py"}


@pytest.mark.parametrize("service", SERVICES)
def test_vendored_caller_guard_is_byte_identical(service):
    """MCP services don't depend on the esmeralda package: they vendor its caller guard unchanged."""
    copy = REPO / "apps/services" / service / "caller_guard.py"
    assert copy.read_bytes() == CANONICAL.read_bytes(), (
        f"{copy.relative_to(REPO)} differs from {CANONICAL.relative_to(REPO)}: copy the package module over it"
    )


def test_caller_guard_uses_the_standard_library_only():
    tree = ast.parse(CANONICAL.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    allowed = {"__future__", "base64", "contextvars", "json", "logging", "os", "sys", "collections", "dataclasses",
               "typing"}
    assert imported <= allowed, f"non-stdlib imports in the vendored guard: {sorted(imported - allowed)}"


@pytest.mark.parametrize("service", SERVICES)
def test_mcp_services_wrap_their_app_with_the_guard(service):
    source = (REPO / "apps/services" / service / SERVICE_APPS[service]).read_text()
    assert "from caller_guard import CallerGuard" in source
    assert re.search(r"^app = CallerGuard\(", source, re.MULTILINE), f"{service}: serve CallerGuard(...) as app"


def test_unregistered_probe_identity_is_granted_nothing():
    """sa-probe-unregistered-<env>: one service account, one binding (the test VM may mint its tokens)."""
    main = (REPO / "infrastructure/modules/3-security/main.tf").read_text()
    resources = re.findall(r'^resource "([^"]+)" "([^"]+)" \{\n(.*?)^\}', main, re.MULTILINE | re.DOTALL)
    probe = [(kind, name, body) for kind, name, body in resources if "probe_unregistered" in name + body]
    assert {(kind, name) for kind, name, _ in probe} == {
        ("google_service_account", "probe_unregistered_sa"),
        ("google_service_account_iam_member", "probe_unregistered_test_vm_token_creator"),
    }
    (_, _, binding), = [p for p in probe if p[0] == "google_service_account_iam_member"]
    assert 'role               = "roles/iam.serviceAccountTokenCreator"' in binding
    assert "google_service_account.test_vm_sa.email" in binding
    assert "service_account_id = google_service_account.probe_unregistered_sa[0].name" in binding
    for kind, _, body in probe:
        assert "count" in body and "var.enable_unregistered_probe" in body, kind


def test_unregistered_probe_is_on_in_dev_and_off_in_prd():
    for env, expected in (("dev", "true"), ("prd", "false")):
        text = (REPO / "infrastructure/live" / env / "env.yaml").read_text()
        assert re.search(rf"unregistered_probe\s*=\s*{expected}\b", text), env


def test_unregistered_probe_never_reaches_a_compiled_binding():
    """Nothing in the units that grant run.invoker / aiplatform.user / iap.egressor may name it."""
    live = REPO / "infrastructure/live"
    for path in live.rglob("terragrunt.hcl"):
        if "layer-3-security" in path.parts:
            continue
        assert "probe_unregistered" not in path.read_text(), path
    for path in (REPO / "catalog/.resolved").glob("*.json"):
        assert "probe_unregistered" not in path.read_text(), path
