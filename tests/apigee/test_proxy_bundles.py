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

"""Offline structural and security conformance tests for apps/apigee/ bundles and capability modules."""

from __future__ import annotations

import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APIGEE_APPS_DIR = REPO_ROOT / "apps" / "apigee"
PROXIES_DIR = APIGEE_APPS_DIR / "proxies"
SHAREDFLOWS_DIR = APIGEE_APPS_DIR / "sharedflows"
CAPABILITIES_MODULES_DIR = REPO_ROOT / "infrastructure" / "modules" / "5-workloads" / "apigee" / "capabilities"


def _find_terraform_binary() -> str:
    env_path = os.environ.get("PATH", "")
    extra_dirs = [str(Path.home() / ".terraform" / "bin"), str(Path.home() / ".terragrunt" / "bin")]
    search_path = ":".join(extra_dirs + [env_path])
    for candidate in ("terraform", "tofu"):
        found = shutil.which(candidate, path=search_path)
        if found:
            return found
    raise RuntimeError("Neither terraform nor tofu was found on PATH or ~/.terraform/bin")


def test_all_xml_files_are_well_formed() -> None:
    """Every XML file under apps/apigee/ must be well-formed XML."""
    xml_files = sorted(APIGEE_APPS_DIR.rglob("*.xml"))
    assert len(xml_files) >= 20, "Expected proxy and shared-flow XML files under apps/apigee/"
    for xml_path in xml_files:
        try:
            ET.parse(xml_path)
        except ET.ParseError as exc:
            pytest.fail(f"Invalid XML in {xml_path.relative_to(REPO_ROOT)}: {exc}")


@pytest.mark.parametrize("proxy_name", ["partner-agents", "partner-mcp"])
def test_every_proxy_requires_verify_api_key_in_preflow(proxy_name: str) -> None:
    """Every proxy must enforce VAK-Verify-Partner-Key in PreFlow (no unauthenticated proxies)."""
    proxy_default = PROXIES_DIR / proxy_name / "apiproxy" / "proxies" / "default.xml"
    root = ET.parse(proxy_default).getroot()
    preflow_steps = [
        el.text.strip()
        for el in root.findall("./PreFlow/Request/Step/Name")
        if el.text
    ]
    assert "VAK-Verify-Partner-Key" in preflow_steps, (
        f"{proxy_name} is missing VAK-Verify-Partner-Key in PreFlow/Request"
    )
    assert preflow_steps[0] == "VAK-Verify-Partner-Key", (
        f"{proxy_name} must run VAK-Verify-Partner-Key as the first PreFlow step"
    )


@pytest.mark.parametrize("proxy_name", ["partner-agents", "partner-mcp"])
def test_every_proxy_logs_in_postclientflow_without_bodies(proxy_name: str) -> None:
    """Every proxy must attach ML-interaction in PostClientFlow and never log request/response bodies."""
    apiproxy_dir = PROXIES_DIR / proxy_name / "apiproxy"
    proxy_default = apiproxy_dir / "proxies" / "default.xml"
    root = ET.parse(proxy_default).getroot()

    post_client_steps = [
        el.text.strip()
        for el in root.findall("./PostClientFlow/Response/Step/Name")
        if el.text
    ]
    assert "ML-interaction" in post_client_steps, (
        f"{proxy_name} must attach ML-interaction in PostClientFlow/Response"
    )

    ml_policy = apiproxy_dir / "policies" / "ML-interaction.xml"
    ml_text = ml_policy.read_text(encoding="utf-8")
    assert "request.content" not in ml_text, f"{proxy_name}/ML-interaction.xml must not log request.content"
    assert "response.content" not in ml_text, f"{proxy_name}/ML-interaction.xml must not log response.content"
    for otel_field in (
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.total_tokens",
        "gen_ai.request.model",
    ):
        assert otel_field in ml_text, f"{proxy_name}/ML-interaction.xml is missing OTel field {otel_field}"


def test_usage_metering_ml_interaction_never_logs_bodies() -> None:
    """Shared-flow ML-interaction must never log request.content or response.content."""
    ml_policy = (
        SHAREDFLOWS_DIR / "usage-metering" / "sharedflowbundle" / "policies" / "ML-interaction.xml"
    )
    ml_text = ml_policy.read_text(encoding="utf-8")
    assert "request.content" not in ml_text
    assert "response.content" not in ml_text


def test_partner_agents_separates_sse_and_non_sse_target_endpoints() -> None:
    """partner-agents must route streaming paths to targets/sse.xml and non-streaming to targets/default.xml."""
    apiproxy_dir = PROXIES_DIR / "partner-agents" / "apiproxy"
    sse_target = apiproxy_dir / "targets" / "sse.xml"
    default_target = apiproxy_dir / "targets" / "default.xml"
    assert sse_target.exists(), "targets/sse.xml must exist"
    assert default_target.exists(), "targets/default.xml must exist"

    proxy_root = ET.parse(apiproxy_dir / "proxies" / "default.xml").getroot()
    route_targets = {
        rule.attrib.get("name"): (rule.findtext("TargetEndpoint") or "").strip()
        for rule in proxy_root.findall("./RouteRule")
    }
    assert route_targets.get("sse") == "sse"
    assert route_targets.get("default") == "default"


def test_sse_targets_enable_streaming_and_300s_timeouts_without_buffering() -> None:
    """Every SSE target must enable streaming, set >=300s timeouts, and avoid unguarded ExtractVariables."""
    sse_targets = [
        PROXIES_DIR / "partner-agents" / "apiproxy" / "targets" / "sse.xml",
        PROXIES_DIR / "partner-mcp" / "apiproxy" / "targets" / "default.xml",
    ]
    for target_path in sse_targets:
        root = ET.parse(target_path).getroot()
        props = {
            prop.attrib.get("name"): (prop.text or "").strip()
            for prop in root.findall("./HTTPTargetConnection/Properties/Property")
        }
        assert props.get("response.streaming.enabled") == "true", (
            f"{target_path.relative_to(REPO_ROOT)} must set response.streaming.enabled=true"
        )
        assert int(props.get("io.timeout.millis", "0")) >= 300000, (
            f"{target_path.relative_to(REPO_ROOT)} must set io.timeout.millis >= 300000"
        )
        # Ensure no unguarded EV-ExtractTokenCount in SSE target PostFlow/PreFlow
        for step in root.findall(".//PostFlow//Step") + root.findall(".//PreFlow//Step"):
            name = (step.findtext("Name") or "").strip()
            condition = (step.findtext("Condition") or "").strip()
            if name == "EV-ExtractTokenCount":
                assert "text/event-stream" in condition, (
                    f"{target_path.relative_to(REPO_ROOT)} has unguarded EV-ExtractTokenCount"
                )


def test_every_target_enforces_strict_tls_validation() -> None:
    """Every TargetEndpoint must enable TLS and set IgnoreValidationErrors=false."""
    target_files = sorted(PROXIES_DIR.glob("*/apiproxy/targets/*.xml"))
    assert len(target_files) == 3
    for target_path in target_files:
        root = ET.parse(target_path).getroot()
        ssl_info = root.find("./HTTPTargetConnection/SSLInfo")
        assert ssl_info is not None, f"{target_path.relative_to(REPO_ROOT)} is missing <SSLInfo>"
        assert (ssl_info.findtext("Enabled") or "").strip() == "true"
        assert (ssl_info.findtext("IgnoreValidationErrors") or "").strip() == "false"


def test_ltq_policies_count_total_tokens_and_set_model_source() -> None:
    """LTQ-count.xml must meter {llm.totalTokenCount} and LTQ-enforce.xml must set <LLMModelSource>."""
    ltq_enforce_files = [
        SHAREDFLOWS_DIR / "token-quota" / "sharedflowbundle" / "policies" / "LTQ-enforce.xml",
        PROXIES_DIR / "partner-agents" / "apiproxy" / "policies" / "LTQ-enforce.xml",
    ]
    ltq_count_files = [
        SHAREDFLOWS_DIR / "token-quota" / "sharedflowbundle" / "policies" / "LTQ-count.xml",
        PROXIES_DIR / "partner-agents" / "apiproxy" / "policies" / "LTQ-count.xml",
    ]

    for enforce_path in ltq_enforce_files:
        root = ET.parse(enforce_path).getroot()
        assert (root.findtext("EnforceOnly") or "").strip() == "true"
        assert (root.findtext("SharedName") or "").strip() == "esmeralda-partner-llm-quota"
        model_source = (root.findtext("LLMModelSource") or "").strip()
        assert model_source, f"{enforce_path.relative_to(REPO_ROOT)} must set <LLMModelSource>"

    for count_path in ltq_count_files:
        root = ET.parse(count_path).getroot()
        assert (root.findtext("CountOnly") or "").strip() == "true"
        assert (root.findtext("SharedName") or "").strip() == "esmeralda-partner-llm-quota"
        usage_source = (root.findtext("LLMTokenUsageSource") or "").strip()
        assert usage_source == "{llm.totalTokenCount}", (
            f"{count_path.relative_to(REPO_ROOT)} must count total tokens ({{llm.totalTokenCount}})"
        )


@pytest.mark.parametrize(
    "capability_module",
    ["partner-agents", "partner-mcp", "token-quota", "usage-metering", "cross-cloud"],
)
def test_capability_terraform_modules_validate_offline(capability_module: str) -> None:
    """All 5 Apigee capability Terraform modules must pass `terraform init -backend=false && terraform validate`."""
    tf_bin = _find_terraform_binary()
    mod_dir = CAPABILITIES_MODULES_DIR / capability_module
    assert (mod_dir / "main.tf").exists(), f"Missing {mod_dir / 'main.tf'}"

    subprocess.run(
        [tf_bin, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=mod_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    validate_res = subprocess.run(
        [tf_bin, "validate", "-no-color"],
        cwd=mod_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Success!" in validate_res.stdout


def test_message_template_literals_are_single_quoted() -> None:
    """firstnonnull() literals must be single-quoted: a double-quoted literal breaks the JSON
    payload of ML-interaction and Cloud Logging silently drops the entry."""
    import re

    bad = re.compile(r"firstnonnull\([^)]*(\"|,\s*\d+\s*\))")
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in sorted(APIGEE_APPS_DIR.rglob("*.xml"))
        if bad.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"Use firstnonnull(var,'literal') in: {offenders}"


def test_partner_mcp_southbound_target() -> None:
    """partner-mcp calls the entry point through ts-esmeralda-mcp on /mcp, with the Host override
    Apigee honours for target servers (target.header.host) and the service ID token audience."""
    apiproxy_dir = PROXIES_DIR / "partner-mcp" / "apiproxy"
    target = ET.parse(apiproxy_dir / "targets" / "default.xml").getroot()
    servers = [s.attrib["name"] for s in target.findall("./HTTPTargetConnection/LoadBalancer/Server")]
    assert servers == ["ts-esmeralda-mcp"]
    assert (target.findtext("./HTTPTargetConnection/Path") or "").strip() == "/mcp"
    audience = target.find("./HTTPTargetConnection/Authentication/GoogleIDToken/Audience")
    assert audience is not None and audience.attrib.get("ref") == "mcp.target.audience"
    steps = [(s.text or "").strip() for s in target.findall("./PreFlow/Request/Step/Name")]
    assert "AM-set-target-host" in steps

    am = ET.parse(apiproxy_dir / "policies" / "AM-set-target-host.xml").getroot()
    assigned = {(v.findtext("Name") or "").strip() for v in am.findall("./AssignVariable")}
    assert {"target.header.host", "target.copy.pathsuffix"} <= assigned

    proxy = ET.parse(apiproxy_dir / "proxies" / "default.xml").getroot()
    conditions = {
        (s.findtext("Name") or "").strip(): (s.findtext("Condition") or "").strip()
        for s in proxy.findall("./PreFlow/Request/Step")
    }
    assert "KVM-Get-MCP-Config" in conditions
    assert conditions.get("RF-Not-Found") == "mcp.service.allowed != true", "unexposed services must not be routed"
