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

"""Repository hygiene, environment manifest parity, and Apigee bundle guards."""

from __future__ import annotations

import pathlib
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEV_ENV_PATH = REPO_ROOT / "infrastructure/live/dev/env.yaml"
PRD_ENV_PATH = REPO_ROOT / "infrastructure/live/prd/env.yaml"
APIGEE_PROXIES_DIR = REPO_ROOT / "apps/apigee/proxies"

BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".pdf",
    ".zip",
}

ALLOWED_CAPABILITY_PROVIDERS = {"none", "agent_gateway", "apigee"}
ALLOWED_APIGEE_ENV_TYPES = {"BASE", "INTERMEDIATE", "COMPREHENSIVE"}
EXPECTED_CAPABILITY_KEYS = {
    "egress_allowlist",
    "content_safety",
    "partner_access",
    "token_quota",
    "usage_metering",
    "cross_cloud",
}

# Match concrete 4-char random hex suffixes on Esmeralda project IDs,
# while allowing suffix-less component names (`esm-dev-net-host`, `esm-dev-mcps`),
# Terragrunt mock IDs (`*-mock`), and placeholders (`<sfx>`).
REAL_PROJECT_ID_RE = re.compile(
    r"\besm-(?:dev|prd|cicd)(?:-[a-z0-9]+)+-(?!host\b|mcps\b|mock\b)[a-z0-9]{4}\b"
)

# Standard 18-hex-char GCP billing account identifier (XXXXXX-XXXXXX-XXXXXX).
BILLING_ACCOUNT_ID_RE = re.compile(r"\b[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}\b")

# Assembled from parts so the guard file itself never trips plain-text diff scanners.
_RESTRICTED_DOMAINS = (
    "alto" + r"strat\.com",
    "corp" + r"\.google\.com",
    "google" + r"plex\.com",
)
RESTRICTED_ADDRESS_RE = re.compile(
    r"(?:[A-Za-z0-9._%+-]+@(?:gmail\.com|googlemail\.com|"
    + "|".join(_RESTRICTED_DOMAINS)
    + r")|\b(?:"
    + "|".join(_RESTRICTED_DOMAINS)
    + r")\b)",
    re.IGNORECASE,
)


def _tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    files: list[pathlib.Path] = []
    for rel in out.splitlines():
        if not rel or rel in {"uv.lock", "tests/guards/test_repo_guards.py"}:
            continue
        path = REPO_ROOT / rel
        if path.is_file() and path.suffix.lower() not in BINARY_SUFFIXES:
            files.append(path)
    return files


def _docs_files() -> list[pathlib.Path]:
    docs_src = REPO_ROOT / "docs/src"
    if not docs_src.exists():
        return []
    return sorted(
        p for p in docs_src.rglob("*") if p.is_file() and p.suffix.lower() not in BINARY_SUFFIXES
    )


def test_no_hardcoded_project_ids_billing_or_restricted_domains():
    """Tracked code and `docs/src/` must not contain suffixed project IDs, billing IDs, or restricted domains."""
    candidates = sorted(set(_tracked_files()) | set(_docs_files()))
    violations: list[str] = []

    for path in candidates:
        rel = path.relative_to(REPO_ROOT)
        text = path.read_text(errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            for label, pattern in (
                ("hardcoded project ID suffix", REAL_PROJECT_ID_RE),
                ("hardcoded billing account ID", BILLING_ACCOUNT_ID_RE),
                ("restricted domain/address", RESTRICTED_ADDRESS_RE),
            ):
                match = pattern.search(line)
                if match:
                    violations.append(f"{rel}:{line_no}: {label} '{match.group(0)}'")

    assert not violations, (
        "Found environment-specific IDs or restricted addresses in tracked files:\n"
        + "\n".join(violations)
    )


def test_guard_regexes_catch_planted_violations():
    """Verify the hygiene regexes detect suffixed project IDs, billing IDs, and internal/demo domains."""
    sample_dev_proj = "esm-dev-governance-" + "9f0a"
    sample_prd_proj = "esm-prd-cx-agents-" + "1b2c"
    assert REAL_PROJECT_ID_RE.search(f"projects/{sample_dev_proj}/locations/us-central1")
    assert REAL_PROJECT_ID_RE.search(sample_prd_proj)
    assert not REAL_PROJECT_ID_RE.search("projects/esm-dev-cx-agents-<sfx>/locations/us-central1")
    assert not REAL_PROJECT_ID_RE.search("esm-dev-ai-coe-agents")
    assert not REAL_PROJECT_ID_RE.search("esm-dev-net-host")
    assert not REAL_PROJECT_ID_RE.search("esm-dev-mcps")
    assert not REAL_PROJECT_ID_RE.search("esm-dev-gateway-mock")

    sample_billing = "01" + "ABCD-23EF45-6789AB"
    assert BILLING_ACCOUNT_ID_RE.search(f'billing_account = "{sample_billing}"')

    for domain in ("alto" + "strat.com", "corp" + ".google.com", "google" + "plex.com"):
        assert RESTRICTED_ADDRESS_RE.search(f"admin@{domain}")
        assert RESTRICTED_ADDRESS_RE.search(f"https:// host.{domain}/path")


# ── HCL `env.yaml` parser for manifest parity checks ─────────────────────────


def _strip_hcl_comments(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        in_str = False
        escaped = False
        out: list[str] = []
        i = 0
        while i < len(line):
            ch = line[i]
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                out.append(ch)
                escaped = True
            elif ch == '"':
                out.append(ch)
                in_str = not in_str
            elif not in_str and ch == "#":
                break
            elif not in_str and line[i : i + 2] == "//":
                break
            else:
                out.append(ch)
            i += 1
        cleaned_lines.append("".join(out))
    return "\n".join(cleaned_lines)


def _tokenize_hcl(text: str) -> list[str]:
    token_re = re.compile(r'"(?:\\.|[^"\\])*"|[={}\[\],()]|[^\s={}\[\],()]+')
    return token_re.findall(_strip_hcl_comments(text))


def _parse_hcl_value(tokens: list[str], idx: int) -> tuple[Any, int]:
    tok = tokens[idx]
    if tok == "{":
        return _parse_hcl_block(tokens, idx + 1)
    if tok == "[":
        items: list[Any] = []
        idx += 1
        while idx < len(tokens) and tokens[idx] != "]":
            if tokens[idx] == ",":
                idx += 1
                continue
            val, idx = _parse_hcl_value(tokens, idx)
            items.append(val)
        return items, idx + 1
    if tok.startswith('"') and tok.endswith('"'):
        return tok[1:-1], idx + 1
    if tok == "true":
        return True, idx + 1
    if tok == "false":
        return False, idx + 1
    if re.fullmatch(r"-?\d+", tok):
        return int(tok), idx + 1
    if re.fullmatch(r"-?\d+\.\d+", tok):
        return float(tok), idx + 1
    # Function call such as get_env("ESMERALDA_BILLING_ACCOUNT", "")
    if idx + 1 < len(tokens) and tokens[idx + 1] == "(":
        depth = 0
        parts: list[str] = []
        while idx < len(tokens):
            t = tokens[idx]
            parts.append(t)
            if t == "(":
                depth += 1
            elif t == ")":
                depth -= 1
                if depth == 0:
                    idx += 1
                    break
            idx += 1
        return "".join(parts), idx
    return tok, idx + 1


def _parse_hcl_block(tokens: list[str], idx: int) -> tuple[dict[str, Any], int]:
    obj: dict[str, Any] = {}
    while idx < len(tokens) and tokens[idx] != "}":
        if tokens[idx] == ",":
            idx += 1
            continue
        key = tokens[idx].strip('"')
        idx += 1
        if idx < len(tokens) and tokens[idx] == "=":
            idx += 1
        val, idx = _parse_hcl_value(tokens, idx)
        obj[key] = val
    return obj, idx + 1


def load_env_locals(path: pathlib.Path) -> dict[str, Any]:
    """Parse the `locals { ... }` block of an Esmeralda `env.yaml` HCL file."""
    tokens = _tokenize_hcl(path.read_text())
    assert len(tokens) >= 3 and tokens[0] == "locals" and tokens[1] == "{", (
        f"{path}: expected top-level `locals {{ ... }}` block"
    )
    parsed, _ = _parse_hcl_block(tokens, 2)
    return parsed


def test_env_manifests_have_key_parity_and_valid_capabilities():
    """`dev/env.yaml` and `prd/env.yaml` must define the exact same schema and valid capability providers."""
    dev = load_env_locals(DEV_ENV_PATH)
    prd = load_env_locals(PRD_ENV_PATH)

    prd_only_computed = {"release_manifest", "image_digests"}
    assert set(dev.keys()) == (set(prd.keys()) - prd_only_computed), (
        f"Top-level keys differ between dev and prd env.yaml: "
        f"dev-only={set(dev) - set(prd)}, prd-only={set(prd) - set(dev) - prd_only_computed}"
    )

    assert "gateways" in dev and "gateways" in prd
    assert set(dev["gateways"].keys()) == set(prd["gateways"].keys()) == {
        "enabled",
        "default",
        "host_rules",
        "hybrid_agent_backend",
        "agent_backend",
    }

    assert "governance" in dev and "governance" in prd
    assert set(dev["governance"].keys()) == set(prd["governance"].keys()) == {
        "apigee",
        "capabilities",
    }
    assert (
        set(dev["governance"]["apigee"].keys())
        == set(prd["governance"]["apigee"].keys())
        == {"enabled", "byo", "environment_type"}
    )
    assert (
        set(dev["governance"]["capabilities"].keys())
        == set(prd["governance"]["capabilities"].keys())
        == EXPECTED_CAPABILITY_KEYS
    )

    for env_name, env_data in (("dev", dev), ("prd", prd)):
        apigee = env_data["governance"]["apigee"]
        caps = env_data["governance"]["capabilities"]

        assert isinstance(apigee["enabled"], bool), f"{env_name}: apigee.enabled must be bool"
        assert isinstance(apigee["byo"], bool), f"{env_name}: apigee.byo must be bool"
        assert apigee["environment_type"] in ALLOWED_APIGEE_ENV_TYPES, (
            f"{env_name}: invalid apigee.environment_type {apigee['environment_type']!r}"
        )

        for cap_name, provider in caps.items():
            assert provider in ALLOWED_CAPABILITY_PROVIDERS, (
                f"{env_name}: capability {cap_name!r} uses invalid provider {provider!r}"
            )
            if not apigee["enabled"]:
                assert provider != "apigee", (
                    f"{env_name}: capability {cap_name!r} cannot be 'apigee' when apigee.enabled is false"
                )

        assert caps["egress_allowlist"] != "none", (
            f"{env_name}: egress_allowlist cannot be 'none'"
        )
        if caps["token_quota"] == "apigee":
            assert apigee["environment_type"] in {"INTERMEDIATE", "COMPREHENSIVE"}, (
                f"{env_name}: token_quota='apigee' requires INTERMEDIATE or COMPREHENSIVE"
            )


# ── Apigee proxy bundle hygiene guard ─────────────────────────────────────────


def validate_apigee_proxy_bundle(apiproxy_dir: pathlib.Path) -> list[str]:
    """Verify an `apiproxy/` bundle has `ML-interaction` in `PostClientFlow` and consumer authentication."""
    errors: list[str] = []
    bundle_name = apiproxy_dir.parent.name

    policies_dir = apiproxy_dir / "policies"
    proxies_dir = apiproxy_dir / "proxies"

    ml_policy = policies_dir / "ML-interaction.xml"
    if not ml_policy.is_file():
        errors.append(f"{bundle_name}: missing policies/ML-interaction.xml")

    auth_policy_types = {"VerifyAPIKey", "OAuthV2", "VerifyJWT"}
    found_auth_policies: set[str] = set()
    if policies_dir.is_dir():
        for pol_xml in sorted(policies_dir.glob("*.xml")):
            try:
                root = ET.fromstring(pol_xml.read_text())
            except ET.ParseError as exc:
                errors.append(f"{bundle_name}: invalid XML in {pol_xml.name}: {exc}")
                continue
            if root.tag in auth_policy_types:
                found_auth_policies.add(pol_xml.stem)

    if not found_auth_policies:
        errors.append(
            f"{bundle_name}: no consumer authentication policy ({', '.join(sorted(auth_policy_types))}) in policies/"
        )

    proxy_xmls = sorted(proxies_dir.glob("*.xml")) if proxies_dir.is_dir() else []
    if not proxy_xmls:
        errors.append(f"{bundle_name}: no ProxyEndpoint XML files in proxies/")
    else:
        for proxy_xml in proxy_xmls:
            try:
                ep_root = ET.fromstring(proxy_xml.read_text())
            except ET.ParseError as exc:
                errors.append(f"{bundle_name}: invalid XML in proxies/{proxy_xml.name}: {exc}")
                continue

            post_client_steps = [
                step.text.strip()
                for step in ep_root.findall("./PostClientFlow//Step/Name")
                if step.text
            ]
            if "ML-interaction" not in post_client_steps:
                errors.append(
                    f"{bundle_name}: proxies/{proxy_xml.name} does not attach ML-interaction in PostClientFlow"
                )

            preflow_steps = {
                step.text.strip()
                for step in ep_root.findall("./PreFlow/Request/Step/Name")
                if step.text
            }
            flow_steps = {
                step.text.strip()
                for step in ep_root.findall("./Flows/Flow/Request/Step/Name")
                if step.text
            }
            if found_auth_policies and not (found_auth_policies & (preflow_steps | flow_steps)):
                errors.append(
                    f"{bundle_name}: proxies/{proxy_xml.name} does not attach any consumer auth policy "
                    f"({sorted(found_auth_policies)}) in Request PreFlow or Flows"
                )

    return errors


def test_apigee_proxy_bundle_validator_catches_missing_logging_or_auth(tmp_path: pathlib.Path):
    """Unit test the Apigee proxy bundle guard against compliant and non-compliant synthetic bundles."""
    good = tmp_path / "partner-agents" / "apiproxy"
    (good / "policies").mkdir(parents=True)
    (good / "proxies").mkdir(parents=True)
    (good / "policies/ML-interaction.xml").write_text("<MessageLogging name='ML-interaction'/>")
    (good / "policies/VA-verify-key.xml").write_text("<VerifyAPIKey name='VA-verify-key'/>")
    (good / "proxies/default.xml").write_text(
        """<ProxyEndpoint name="default">
          <PreFlow name="PreFlow">
            <Request><Step><Name>VA-verify-key</Name></Step></Request>
          </PreFlow>
          <PostClientFlow name="PostClientFlow">
            <Response><Step><Name>ML-interaction</Name></Step></Response>
          </PostClientFlow>
        </ProxyEndpoint>"""
    )
    assert validate_apigee_proxy_bundle(good) == []

    bad = tmp_path / "unauthenticated-proxy" / "apiproxy"
    (bad / "policies").mkdir(parents=True)
    (bad / "proxies").mkdir(parents=True)
    (bad / "proxies/default.xml").write_text("<ProxyEndpoint name='default'><PreFlow/></ProxyEndpoint>")
    errs = validate_apigee_proxy_bundle(bad)
    assert any("missing policies/ML-interaction.xml" in e for e in errs)
    assert any("no consumer authentication policy" in e for e in errs)
    assert any("does not attach ML-interaction in PostClientFlow" in e for e in errs)


def test_discovered_apigee_proxy_bundles_meet_hygiene_contract():
    """Every `apps/apigee/proxies/*/apiproxy` bundle must enforce consumer auth and PostClientFlow logging."""
    bundles = sorted(APIGEE_PROXIES_DIR.glob("*/apiproxy")) if APIGEE_PROXIES_DIR.exists() else []
    if not bundles:
        pytest.skip("No Apigee proxy bundles present under apps/apigee/proxies/*/apiproxy in this branch.")

    all_errors: list[str] = []
    for bundle in bundles:
        all_errors.extend(validate_apigee_proxy_bundle(bundle))

    assert not all_errors, "Apigee proxy bundle hygiene violations:\n" + "\n".join(all_errors)
