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

"""Contract tests for the Model Armor switches (env.yaml governance.model_armor).

* prd keeps both inspection points off; dev never turns the gateway callout on by default.
* No hard-coded ``count = 0`` is left on the Agent Gateway CONTENT_AUTHZ resources; they follow the
  switch, with a configurable fail_open and timeout.
* The CONTENT_AUTHZ policy is scoped to unary MCP tools/call / prompts/get and agent cards, so A2A
  messages, model calls and streams never open a Model Armor callout.
* The switch logic in ``model_armor.tf`` is evaluated offline with Terraform.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GOVERNANCE_MODULE = REPO_ROOT / "infrastructure/modules/4-governance"
MODEL_ARMOR_TF = GOVERNANCE_MODULE / "model_armor.tf"
AGW_MAIN_TF = GOVERNANCE_MODULE / "modules/6_agent_gateway/main.tf"
FLOOR_TF = GOVERNANCE_MODULE / "modules/5_model_armor/vertex_floor_settings.tf"
FLOOR_OFF_SCRIPT = REPO_ROOT / "infrastructure/modules/_shared/scripts/model_armor_floor_off.sh"
ENV_PATHS = {env: REPO_ROOT / f"infrastructure/live/{env}/env.yaml" for env in ("dev", "prd")}
AGENT_UNITS = sorted(REPO_ROOT.glob("infrastructure/live/*/layer-5-workloads/agents/*/terragrunt.hcl"))
AGENT_MODULES = [REPO_ROOT / f"infrastructure/modules/5-workloads/agents/{m}" for m in ("adk-agent", "a2a-agent")]

MODEL_ARMOR_KEYS = {"model_calls", "model_calls_sdp", "gateway", "gateway_fail_open", "gateway_timeout_ms"}


def _load_env_locals(path: pathlib.Path) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("_repo_guards", REPO_ROOT / "tests/guards/test_repo_guards.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_env_locals(path)


def _resource_block(text: str, rtype: str, name: str) -> str:
    """The body of ``resource "<rtype>" "<name>" { ... }`` (brace matched)."""
    start = text.index(f'resource "{rtype}" "{name}"')
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i : j + 1]
    raise AssertionError(f"unbalanced braces in {rtype}.{name}")


def _code(text: str) -> str:
    """HCL without comment lines."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


# ── env.yaml defaults ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def model_armor_env() -> dict[str, dict[str, Any]]:
    return {env: _load_env_locals(path)["governance"]["model_armor"] for env, path in ENV_PATHS.items()}


def test_model_armor_block_has_the_same_keys_in_every_env(model_armor_env):
    for env, block in model_armor_env.items():
        assert set(block) == MODEL_ARMOR_KEYS, f"{env}: governance.model_armor keys {sorted(block)}"


def test_prd_defaults_are_off(model_armor_env):
    prd = model_armor_env["prd"]
    assert prd["model_calls"] == "off"
    assert prd["gateway"] == "off"
    assert prd["gateway_fail_open"] is False, "prd: the recommended (and default) gateway_fail_open is false"


def test_dev_defaults(model_armor_env):
    dev = model_armor_env["dev"]
    assert dev["model_calls"] in {"inspect", "enforce"}, "dev showcases Vertex AI built-in Model Armor"
    # The CONTENT_AUTHZ callout stays off by default until the long-call regression is proven live.
    assert dev["gateway"] == "off"
    assert dev["gateway_fail_open"] is True
    assert dev["model_calls_sdp"] is False, "SDP blocks (never masks) and the demo data looks like PII"


def test_env_values_are_valid(model_armor_env):
    for env, block in model_armor_env.items():
        assert block["model_calls"] in {"off", "inspect", "enforce"}, env
        assert block["gateway"] in {"off", "on"}, env
        assert isinstance(block["gateway_fail_open"], bool), env
        assert isinstance(block["model_calls_sdp"], bool), env
        assert 10 <= block["gateway_timeout_ms"] <= 10000, env


# ── Agent Gateway CONTENT_AUTHZ ───────────────────────────────────────────────


def test_no_hard_coded_count_zero_on_model_armor_resources():
    text = AGW_MAIN_TF.read_text()
    ext = _code(_resource_block(text, "google_network_services_authz_extension", "model_armor_content_authz"))
    policy = _code(_resource_block(text, "google_network_security_authz_policy", "model_armor_policy"))
    assert not re.search(r"^\s*count\s*=\s*0\s*$", ext, re.M)
    assert not re.search(r"^\s*count\s*=\s*0\s*$", policy, re.M)
    assert re.search(r"^\s*count\s*=.*var\.model_armor_gateway", ext, re.M)
    assert "google_network_services_authz_extension.model_armor_content_authz" in policy.split("custom_provider")[0]


def test_content_authz_fail_open_and_timeout_follow_the_switch():
    text = AGW_MAIN_TF.read_text()
    ext = _code(_resource_block(text, "google_network_services_authz_extension", "model_armor_content_authz"))
    assert re.search(r"^\s*fail_open\s*=\s*var\.model_armor_fail_open\s*$", ext, re.M)
    assert re.search(r"^\s*timeout\s*=.*var\.model_armor_timeout_ms", ext, re.M)
    assert "request_template_id" in ext and "response_template_id" in ext


def test_content_authz_policy_is_scoped_to_unary_routes():
    text = AGW_MAIN_TF.read_text()
    policy = _code(_resource_block(text, "google_network_security_authz_policy", "model_armor_policy"))
    assert 'policy_profile = "CONTENT_AUTHZ"' in policy
    rules = re.findall(r"http_rules\s*\{", policy)
    assert len(rules) == 2, "one rule for MCP, one for agent cards"
    assert re.findall(r'name\s*=\s*"([^"]+)"', policy.split("custom_provider")[0])[1:] == ["tools/call", "prompts/get"]
    assert 'exact = "/mcp"' in policy
    assert 'exact = "/v1/card"' in policy
    # Never match A2A messages (long, and not inspected for A2A < v1) or streams.
    for forbidden in ("message:send", "message:stream", "prefix = \"/\"", "streamGenerateContent", "streamQuery"):
        assert forbidden not in policy, forbidden


def test_floor_setting_follows_model_calls():
    text = FLOOR_TF.read_text()
    floor = _code(_resource_block(text, "google_model_armor_floorsetting", "vertex_ai"))
    assert 'integrated_services              = ["AI_PLATFORM"]' in floor
    assert 'inspect_only         = var.model_calls == "inspect"' in floor
    assert 'inspect_and_block    = var.model_calls == "enforce"' in floor
    assert "var.model_calls_sdp" in floor
    assert 'var.model_calls == "off" ? toset([])' in text
    off = _code(_resource_block(text, "terraform_data", "vertex_ai_floor_off"))
    assert "when    = destroy" in off and "model_armor_floor_off.sh" in off
    assert FLOOR_OFF_SCRIPT.is_file()


def test_agents_get_the_regional_model_location_from_layer_4():
    assert len(AGENT_UNITS) == 4, AGENT_UNITS
    wanted = 'model_location = try(dependency.governance.outputs.model_armor_model_location, "")'
    for unit in AGENT_UNITS:
        assert wanted in unit.read_text(), unit
    for module in AGENT_MODULES:
        assert "MODEL_LOCATION = var.model_location" in (module / "main.tf").read_text(), module
        assert 'variable "model_location"' in (module / "variables.tf").read_text(), module


def test_l4_units_pass_the_model_armor_block():
    for env in ENV_PATHS:
        unit = REPO_ROOT / f"infrastructure/live/{env}/layer-4-governance/terragrunt.hcl"
        assert "model_armor_config           = try(local.env.governance.model_armor, {})" in unit.read_text(), env


# ── model_armor.tf evaluated offline ──────────────────────────────────────────

STUB_TF = """
variable "region" {
  type    = string
  default = "us-central1"
}
variable "agent_project_ids" {
  type    = list(string)
  default = ["esm-dev-cx-agents-mock"]
}
variable "governance_config" {
  type = object({
    capabilities = optional(object({
      content_safety = optional(string, "agent_gateway")
    }), {})
  })
  default = {}
}
"""


def _tf_binary() -> str:
    if os.environ.get("TF_BIN") and shutil.which(os.environ["TF_BIN"]):
        return shutil.which(os.environ["TF_BIN"])  # type: ignore[return-value]
    for candidate in (os.fspath(pathlib.Path.home() / ".terraform/bin/terraform"), "terraform", "tofu"):
        if found := shutil.which(candidate):
            return found
    pytest.exit("tests/governance requires `terraform` or `tofu` on PATH (or TF_BIN)", returncode=2)


def _evaluate(workdir: pathlib.Path, tfvars: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MODEL_ARMOR_TF, workdir / "model_armor.tf")
    (workdir / "stub.tf").write_text(STUB_TF)
    (workdir / "terraform.tfvars.json").write_text(json.dumps(tfvars))
    env = {**os.environ, "TF_IN_AUTOMATION": "1"}
    tf = _tf_binary()
    init = subprocess.run([tf, f"-chdir={workdir}", "init", "-backend=false", "-input=false", "-no-color"],
                          capture_output=True, text=True, env=env)
    assert init.returncode == 0, init.stdout + init.stderr
    return subprocess.run([tf, f"-chdir={workdir}", "apply", "-auto-approve", "-input=false", "-no-color"],
                          capture_output=True, text=True, env=env)


def _outputs(workdir: pathlib.Path) -> dict[str, Any]:
    res = subprocess.run([_tf_binary(), f"-chdir={workdir}", "output", "-json"], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return {k: v["value"] for k, v in json.loads(res.stdout).items()}


def test_defaults_are_off(tmp_path: pathlib.Path):
    res = _evaluate(tmp_path, {})
    assert res.returncode == 0, res.stdout + res.stderr
    out = _outputs(tmp_path)
    assert out["model_armor_model_location"] == ""
    assert out["model_armor"]["model_calls"] == "off"
    assert out["model_armor"]["gateway"] == "off"
    assert out["model_armor"]["gateway_fail_open"] is False
    assert out["model_armor"]["gateway_timeout_ms"] == 10000


def test_model_calls_on_moves_agents_to_the_regional_endpoint(tmp_path: pathlib.Path):
    res = _evaluate(tmp_path, {"model_armor_config": {"model_calls": "inspect", "gateway": "on"}})
    assert res.returncode == 0, res.stdout + res.stderr
    out = _outputs(tmp_path)
    assert out["model_armor_model_location"] == "us-central1"
    assert out["model_armor"]["gateway"] == "on"


def test_gateway_needs_content_safety_on_agent_gateway(tmp_path: pathlib.Path):
    res = _evaluate(tmp_path, {
        "model_armor_config": {"gateway": "on"},
        "governance_config": {"capabilities": {"content_safety": "none"}},
    })
    assert res.returncode == 0, res.stdout + res.stderr
    assert _outputs(tmp_path)["model_armor"]["gateway"] == "off"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"model_calls": "block"}, "model_calls must be one of"),
        ({"gateway": "true"}, "gateway must be"),
        ({"gateway_timeout_ms": 20000}, "between 10 and 10000"),
    ],
)
def test_invalid_values_are_rejected(tmp_path: pathlib.Path, config: dict[str, Any], message: str):
    res = _evaluate(tmp_path, {"model_armor_config": config})
    assert res.returncode != 0
    assert message in " ".join((res.stdout + res.stderr).split())
