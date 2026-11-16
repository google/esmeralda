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

"""Agent Registry descriptions: every entry Esmeralda registers says why it exists and who uses it.

The descriptions come from the catalog (the known-hosts table for Google APIs, the target
``description`` for MCP servers, agents and ``managed: false`` targets), compiled into
``catalog/.resolved/<env>.json`` with a generated "Used by" suffix, and every
``google_agent_registry_service`` resource passes one.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from esmeralda import catalog

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIVE = REPO_ROOT / "infrastructure/live"
MODULES = REPO_ROOT / "infrastructure/modules"
LIVE_ENVS = sorted(p.parent.name for p in LIVE.glob("*/env.yaml") if p.parent.name != "shared")
LIMIT = catalog.resolver.REGISTRY_DESCRIPTION_MAX


def _resolved(env: str) -> dict:
    return json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())


def _check(where: str, description: object, callers: list[str]) -> None:
    assert isinstance(description, str) and description.strip(), f"{where}: no description"
    assert len(description) <= LIMIT, f"{where}: description over {LIMIT} characters"
    assert "Used by" in description, f"{where}: description doesn't name its callers"
    for caller in callers:
        assert caller in description, f"{where}: description doesn't name caller {caller}"


def test_every_known_google_api_has_a_description():
    for name, api in catalog.GOOGLE_APIS.items():
        assert api.description.strip(), name
        assert len(api.description) <= catalog.resolver.TARGET_DESCRIPTION_MAX, name


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_every_compiled_registry_entry_has_a_description(env):
    data = _resolved(env)
    resolved = catalog.resolve(REPO_ROOT, env)
    for sid, entry in data["google_apis"].items():
        _check(f"{env} google_apis.{sid}", entry.get("description"), entry["callers"])
        assert entry["description"].startswith(catalog.GOOGLE_APIS[entry["api"]].description), sid
    for name, entry in data["external_targets"].items():
        _check(f"{env} external_targets.{name}", entry.get("description"),
               list(resolved.targets[name].get("callers") or {}))
    for name, entry in data["catalog"].items():
        _check(f"{env} catalog.{name}", entry.get("description"), list(resolved.targets[name].get("callers") or {}))


def _registry_resources() -> list[tuple[pathlib.Path, str, str]]:
    """(file, resource name, body) of every google_agent_registry_service in the modules."""
    out = []
    for path in sorted(MODULES.rglob("*.tf")):
        text = path.read_text()
        for match in re.finditer(r'^resource "google_agent_registry_service" "([a-z0-9_]+)" \{\n', text, re.MULTILINE):
            depth, i = 1, match.end()
            while depth and i < len(text):
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            out.append((path, match[1], text[match.end():i]))
    return out


def test_every_registry_resource_sets_a_description():
    resources = _registry_resources()
    assert len(resources) >= 5, "expected the layer-4, iap-egress, MCP and agent registry resources"
    for path, name, body in resources:
        top = "\n".join(line for line in body.splitlines() if re.match(r"^  [a-z]", line))
        rel = path.relative_to(REPO_ROOT)
        assert re.search(r"^  description\s*=\s*\S", top, re.MULTILINE), f"{rel}: {name} has no description"
        assert not re.search(r'^  description\s*=\s*""', top, re.MULTILINE), f"{rel}: {name} has an empty description"


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_units_pass_the_catalog_descriptions(env):
    """MCP and A2A agent units read their registry description from the compiled catalog."""
    data = _resolved(env)
    pattern = re.compile(r"^\s*registry_description\s*=\s*try\(local\.(env_vars\.locals\.)?catalog\[local\.name\]"
                         r"\.description", re.MULTILINE)
    for entry in data["catalog"].values():
        if entry["kind"] not in ("mcp", "a2a"):
            continue
        unit = (LIVE / env / "layer-5-workloads" / entry["unit"] / "terragrunt.hcl").read_text()
        assert pattern.search(unit), f"{env} {entry['unit']}: registry_description must come from the catalog"
    layer4 = (LIVE / env / "layer-4-governance/terragrunt.hcl").read_text()
    assert "google_api_endpoints = local.env.catalog_google_apis" in layer4
