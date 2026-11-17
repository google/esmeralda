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

"""Guards against OTLP collector endpoints that nothing deploys.

Workloads export telemetry straight to the Google Cloud Telemetry API (telemetry.googleapis.com)
with their own identity. An ``OTEL_EXPORTER_OTLP_*ENDPOINT`` may only point at a collector that
Terraform deploys, so its value must come from a variable or a unit output, never from a literal
host written into a module, a unit, an ``env.yaml`` or an image.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULES = REPO_ROOT / "infrastructure/modules"
LIVE = REPO_ROOT / "infrastructure/live"
SERVICES = REPO_ROOT / "apps/services"
MCP_SERVICE = MODULES / "5-workloads/services/mcp-service/main.tf"

OTLP_VAR = r"OTEL_EXPORTER_OTLP(?:_[A-Z]+)?_ENDPOINT"
# env { name = "OTEL_EXPORTER_OTLP_ENDPOINT" value = "<literal>" } (value within the same block).
TF_ENV_BLOCK_LITERAL = re.compile(rf'name\s*=\s*"{OTLP_VAR}"\s*\n\s*value\s*=\s*"', re.MULTILINE)
# Map or assignment style: OTEL_EXPORTER_OTLP_ENDPOINT = "<literal>" / "OTEL_...": "<literal>".
ASSIGN_LITERAL = re.compile(rf'"?{OTLP_VAR}"?\s*[=:]\s*"[^"$]')
# Literal URLs of a collector on a made-up internal name (the old default) or the OTLP ports.
COLLECTOR_LITERAL = re.compile(r"""["'](?:https?://)?[a-z0-9.-]+(?:\.internal|\.local)?:(?:4317|4318)\b""")
UNIT_INPUT = re.compile(r"^\s*otlp_endpoint\s*=\s*(.+)$", re.MULTILINE)
ENV_YAML_VALUE = re.compile(r'^\s*otlp_endpoint\s*=\s*"([^"]*)"', re.MULTILINE)


def _strip_hcl_comments(text: str) -> str:
    """Drops full-line `#`/`//` comments and trailing ` # ...` comments (URLs keep their `//`)."""
    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*(#|//)", line):
            continue
        lines.append(re.sub(r"\s#.*$", "", line))
    return "\n".join(lines)


def _literal_offenses(text: str) -> list[str]:
    return [p.pattern for p in (TF_ENV_BLOCK_LITERAL, ASSIGN_LITERAL, COLLECTOR_LITERAL) if p.search(text)]


def _iac_files() -> list[pathlib.Path]:
    files = [*MODULES.rglob("*.tf"), *LIVE.rglob("*.hcl"), *LIVE.rglob("env.yaml")]
    return sorted(p for p in files if ".terraform" not in p.parts and ".terragrunt-cache" not in p.parts)


def test_patterns_catch_hard_coded_endpoints() -> None:
    old_module = 'env {\n  name  = "OTEL_EXPORTER_OTLP_ENDPOINT"\n  value = "http://collector.telemetry.internal:4317"\n}\n'
    assert TF_ENV_BLOCK_LITERAL.search(old_module) and COLLECTOR_LITERAL.search(old_module)
    assert ASSIGN_LITERAL.search('extra_env_vars = { OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = "https://otel.example" }')
    assert ENV_YAML_VALUE.findall('  telemetry = {\n    otlp_endpoint = "http://otel.example:4318"\n  }') == [
        "http://otel.example:4318"
    ]
    wired = 'otlp_env = var.otlp_endpoint == "" ? {} : { OTEL_EXPORTER_OTLP_ENDPOINT = var.otlp_endpoint }'
    assert not _literal_offenses(wired)


def test_no_literal_otlp_endpoint_in_terraform_or_terragrunt() -> None:
    files = _iac_files()
    assert files, "no Terraform or Terragrunt files found"
    offenders = [
        f"{path.relative_to(REPO_ROOT)} ({', '.join(found)})"
        for path in files
        if (found := _literal_offenses(_strip_hcl_comments(path.read_text())))
    ]
    assert not offenders, (
        "OTLP endpoint written as a literal (no collector is deployed at a hard-coded address): "
        f"{offenders}. Pass it through the otlp_endpoint variable from the output of the collector unit."
    )


def test_mcp_service_sets_otlp_endpoint_only_from_its_variable() -> None:
    text = _strip_hcl_comments(MCP_SERVICE.read_text())
    assert not re.search(rf'name\s*=\s*"{OTLP_VAR}"', text), (
        "mcp-service must not set OTEL_EXPORTER_OTLP_ENDPOINT in a static env block; it comes from "
        "local.otlp_env, which is empty unless var.otlp_endpoint is set"
    )
    assert re.search(r'otlp_env\s*=\s*var\.otlp_endpoint\s*==\s*""\s*\?\s*\{\}', text), (
        "mcp-service must leave OTEL_EXPORTER_OTLP_ENDPOINT unset when var.otlp_endpoint is empty"
    )
    assert "local.otlp_env" in text, "mcp-service must merge local.otlp_env into the container env"


def test_service_units_take_otlp_endpoint_from_env_yaml_or_an_output() -> None:
    units = sorted(LIVE.glob("*/layer-5-workloads/services/*/terragrunt.hcl"))
    assert units, "no service units found"
    for path in units:
        text = _strip_hcl_comments(path.read_text())
        values = UNIT_INPUT.findall(text)
        assert values, f"{path.relative_to(REPO_ROOT)}: pass otlp_endpoint (env.yaml telemetry.otlp_endpoint)"
        for value in values:
            value = value.strip()
            assert value.startswith(("try(local.env_vars.locals.telemetry.otlp_endpoint", "dependency.")), (
                f"{path.relative_to(REPO_ROOT)}: otlp_endpoint = {value}; take it from env.yaml "
                "telemetry.otlp_endpoint or from a collector unit output (dependency.<unit>.outputs...)"
            )


def test_env_yaml_otlp_endpoint_is_empty_until_a_collector_is_deployed() -> None:
    env_files = sorted(LIVE.glob("*/env.yaml"))
    assert env_files, "no env.yaml found"
    for path in env_files:
        for value in ENV_YAML_VALUE.findall(path.read_text()):
            assert value == "", (
                f"{path.relative_to(REPO_ROOT)}: telemetry.otlp_endpoint = {value!r}. No collector unit "
                "exists yet, so nothing serves that address; leave it empty (export straight to the "
                "Google Cloud Telemetry API) or wire the collector unit's output in the service units"
            )


def test_mcp_services_have_no_literal_collector_endpoint() -> None:
    sources = sorted(SERVICES.glob("*/otel_setup.py"))
    assert sources, "no apps/services/*/otel_setup.py found"
    for path in sources:
        text = path.read_text()
        literals = set(re.findall(r"""endpoint\s*=\s*["']([^"']+)["']""", text))
        literals |= set(re.findall(r"""_ENDPOINT\s*=\s*["']([^"']+)["']""", text))
        assert literals <= {"telemetry.googleapis.com:443"}, (
            f"{path.relative_to(REPO_ROOT)}: literal OTLP endpoints {sorted(literals)}; the only built-in "
            "target is the Google Cloud Telemetry API, a collector comes from OTEL_EXPORTER_OTLP_ENDPOINT"
        )
        assert 'os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"' in text, (
            f"{path.relative_to(REPO_ROOT)}: must read OTEL_EXPORTER_OTLP_ENDPOINT (set by Terraform only "
            "for a collector it deploys) and fall back to the Telemetry API when it is unset"
        )
        assert not COLLECTOR_LITERAL.search(text), f"{path.relative_to(REPO_ROOT)}: literal collector address"
