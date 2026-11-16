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

"""Model gateway guards: governance.capabilities.model_gateway = "apigee" and the catalog agree.

With "apigee", agents send their Gemini calls to https://apigee.<domain>/llm through the Central
Agent Gateway, which denies hosts missing from the Agent Registry. The catalog's `llm-gateway`
target (managed: false) is what registers that host and grants the agents roles/iap.egressor on
it, and its callers are also the llm-gateway proxy's allowlist. So it must exist exactly where the
capability is "apigee", with every deployed agent as a caller.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIVE = REPO_ROOT / "infrastructure/live"
LIVE_ENVS = sorted(p.parent.name for p in LIVE.glob("*/env.yaml") if p.parent.name != "shared")
TARGET = "llm-gateway"
GATEWAY_URL = "https://apigee.{domain}/llm"
PROXY_BASE_PATH = "/llm"


def _code(path: pathlib.Path) -> str:
    return "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())


def _model_gateway(env: str) -> str:
    """The effective model_gateway of an env: "apigee" only with Apigee enabled (as the units gate it)."""
    code = _code(LIVE / env / "env.yaml")
    capability = re.search(r'^\s*model_gateway\s*=\s*"([a-z_]+)"', code, re.MULTILINE)
    apigee = re.search(r"^\s*apigee\s*=\s*\{[^}]*?\benabled\s*=\s*(true|false)", code, re.MULTILINE | re.DOTALL)
    value = capability.group(1) if capability else "none"
    return value if value != "apigee" or (apigee and apigee.group(1) == "true") else "none"


def _resolved(env: str) -> dict:
    return json.loads((REPO_ROOT / "catalog/.resolved" / f"{env}.json").read_text())


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_catalog_declares_the_model_gateway_exactly_where_it_is_enabled(env):
    data = _resolved(env)
    declared = TARGET in data["external_targets"]
    enabled = _model_gateway(env) == "apigee"
    assert declared == enabled, (
        f"{env}: model_gateway = {_model_gateway(env)!r} but the catalog "
        f"{'declares' if declared else 'does not declare'} the {TARGET} target "
        f"(catalog/environments/{env}.yaml): agents could not reach the gateway, or a host nobody serves "
        "would be open"
    )


@pytest.mark.parametrize("env", [e for e in LIVE_ENVS if _model_gateway(e) == "apigee"])
def test_model_gateway_target_shape(env):
    """An http endpoint on apigee.{domain} at the proxy's base path, callable by every deployed agent."""
    data = _resolved(env)
    entry = data["external_targets"][TARGET]
    assert entry["kind"] == "http" and entry["registry"] == "endpoint", entry
    assert entry["url"] == GATEWAY_URL
    assert entry["owner_team"] == "platform"

    agents = sorted(name for name, e in data["catalog"].items() if e["kind"] in ("a2a", "adk"))
    expected = sorted(f"{data['catalog'][a]['unit']}:agent_identity_principal" for a in agents)
    assert sorted(data["iap_egress"]["endpoints"][TARGET]) == expected, (
        f"{env}: every agent calls models, so every agent must be a caller of {TARGET}"
    )


@pytest.mark.parametrize("env", LIVE_ENVS)
def test_llm_gateway_unit_maps_every_catalog_caller(env):
    """The proxy allowlist comes from the catalog callers; each ref needs a dependency and a mapping."""
    code = _code(LIVE / env / "layer-5-workloads/apigee/capabilities/llm-gateway/terragrunt.hcl")
    assert 'catalog_iap_egress.endpoints["llm-gateway"]' in code
    assert "agent_principals" in code
    assert "_sa_email\"], \"\")]" not in code, "agents call models as their Agent Identity, not a service account"
    refs = _resolved(env)["iap_egress"]["endpoints"].get(TARGET, [])
    for ref in refs:
        unit, output = ref.split(":")
        assert output == "agent_identity_principal", ref
        dep = unit.rsplit("/", 1)[-1].replace("-", "_")
        assert f'dependency "{dep}"' in code and f'config_path = "../../../{unit}"' in code, ref
        assert f'"{ref}"' in code and f"dependency.{dep}.outputs.agent_identity_principal" in code, ref


@pytest.mark.parametrize("env", LIVE_ENVS)
@pytest.mark.parametrize("agent", ["cx-mortgage-orchestrator", "ai-coe-mortgage-specialist"])
def test_agents_call_the_catalog_gateway_url(env, agent):
    """ESMERALDA_MODEL_GATEWAY_URL is the catalog URL, set only when the capability is "apigee"."""
    code = _code(LIVE / env / "layer-5-workloads/agents" / agent / "terragrunt.hcl")
    assert (
        'capabilities.model_gateway, "none") == "apigee" ? '
        '"https://apigee.${dependency.networking.outputs.entrypoint_domain}/llm" : ""'
    ) in code


def test_proxy_base_path_matches_the_catalog_url():
    manifest = (REPO_ROOT / "apps/apigee/proxies/llm-gateway/apiproxy/llm-gateway.xml").read_text()
    assert f"<BasePaths>{PROXY_BASE_PATH}</BasePaths>" in manifest
    assert GATEWAY_URL.endswith(PROXY_BASE_PATH)
