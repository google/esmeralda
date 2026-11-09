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

"""Catalog guards: the committed catalog/.resolved/<env>.json files match catalog/, and env.yaml reads them."""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from esmeralda import catalog

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIVE = REPO_ROOT / "infrastructure/live"
LIVE_ENVS = sorted(p.parent.name for p in LIVE.glob("*/env.yaml") if p.parent.name != "shared")


def test_catalog_environments_match_live_environments():
    assert catalog.resolver.environments(REPO_ROOT) == LIVE_ENVS


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_resolved_catalog_is_up_to_date(env):
    ok, message = catalog.check_resolved(REPO_ROOT, env)
    assert ok, message


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_env_yaml_reads_the_resolved_catalog(env):
    text = (LIVE / env / "env.yaml").read_text()
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert re.search(r'^\s*catalog_sources\s*=\s*"catalog/sources\.yaml"', code, re.MULTILINE)
    expected = f'lookup(jsondecode(file("${{get_repo_root()}}/catalog/.resolved/{env}.json")), "catalog")'
    assert re.search(r"^\s*catalog\s*=\s*" + re.escape(expected) + r"\s*$", code, re.MULTILINE), (
        f"infrastructure/live/{env}/env.yaml must set catalog = {expected}"
    )
    assert not re.search(r"^\s*catalog\s*=\s*\{", code, re.MULTILINE), "inline catalog maps belong in catalog/"


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_resolved_catalog_keeps_the_unit_contract(env):
    """Every compiled entry has the shape the layer-5 units read, and its unit exists in the env."""
    data = json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())
    assert data["environment"] == env
    for name, entry in data["catalog"].items():
        assert set(entry) <= {"kind", "unit", "token", "probe", "timeout", "callers"}, name
        assert {"kind", "unit", "token", "probe"} <= set(entry), name
        assert (LIVE / env / "layer-5-workloads" / entry["unit"] / "terragrunt.hcl").is_file(), entry["unit"]
