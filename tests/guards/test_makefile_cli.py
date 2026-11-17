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

"""The CLI owns every environment and image operation; the Makefile only holds aliases.

No Makefile recipe may call Terragrunt, ``gcloud builds`` or ``gcloud run deploy`` directly: those
go through ``esmeralda deploy|apply|plan|destroy|build|release`` so the destroy guard, timeouts and
summaries apply. Genuine exceptions go in ``ALLOWED`` with the reason.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"

FORBIDDEN = {
    "terragrunt": re.compile(r"(?<![\w./-])terragrunt(?![\w./-])"),
    "gcloud builds": re.compile(r"\bgcloud\s+(?:[-\w=]+\s+)*builds\b"),
    "gcloud run deploy": re.compile(r"\bgcloud\s+(?:[-\w=]+\s+)*run\s+deploy\b"),
}

# target -> reason. Keep it empty unless a recipe genuinely can't go through the CLI.
ALLOWED: dict[str, str] = {}

# Targets that touch an environment or an image: each must be an `esmeralda` alias.
CLI_ALIASES = {
    "deploy-cicd",
    "deploy-projects",
    "deploy-networking",
    "deploy-security",
    "deploy-foundations",
    "deploy-governance",
    "deploy-workloads",
    "deploy-services",
    "deploy-ai-coe-mortgage-specialist",
    "deploy-cx-mortgage-orchestrator",
    "deploy-agents",
    "deploy-gateway",
    "deploy-entrypoint",
    "deploy-iap-egress",
    "deploy-all",
    "destroy-all",
    "build-ai-coe-mortgage-specialist",
    "build-cx-mortgage-orchestrator",
    "build-agents",
    "build-service-income-verification",
    "build-service-corporate-email",
    "build-service-legacy-dms",
    "build-service-envoy",
    "build-services",
    "build-package",
    "verify-images",
    "status-release",
    "promote",
    "status",
    "doctor",
    "units",
    "plan",
    "apply",
    "verify",
    "logs",
    "guard",
    "test-partner",
    "ui",
}

_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?!=)")


def recipes() -> dict[str, str]:
    """``target -> recipe text`` (continuation lines joined; ``define`` blocks count as recipe text)."""
    found: dict[str, list[str]] = {}
    current = "<define>"
    in_define = False
    for line in MAKEFILE.read_text().splitlines():
        if line.startswith("define "):
            in_define, current = True, f"<define {line.split()[1]}>"
            continue
        if in_define:
            if line.strip() == "endef":
                in_define = False
            else:
                found.setdefault(current, []).append(line)
            continue
        match = _TARGET_RE.match(line)
        if match:
            current = match.group(1)
            found.setdefault(current, [])
        elif line.startswith("\t"):
            found.setdefault(current, []).append(line)
    return {target: "\n".join(lines) for target, lines in found.items()}


def test_recipes_parse():
    parsed = recipes()
    assert {"deploy-all", "build-images", "promote", "clean"} <= set(parsed)


def test_no_recipe_calls_terragrunt_or_gcloud_builds_or_run_deploy_directly():
    offenders = [
        f"{target}: {name}"
        for target, text in recipes().items()
        for name, pattern in FORBIDDEN.items()
        if pattern.search(text) and target not in ALLOWED
    ]
    assert not offenders, (
        "Makefile recipes must call the esmeralda CLI (deploy/apply/build/release), not these tools directly: "
        f"{offenders}. Add a CLI command, or an ALLOWED entry with the reason."
    )


def test_allowlist_has_reasons_and_no_stale_entries():
    parsed = recipes()
    for target, reason in ALLOWED.items():
        assert reason.strip(), f"ALLOWED[{target!r}] needs a reason"
        assert target in parsed, f"ALLOWED[{target!r}] is not a Makefile target any more"
        assert any(p.search(parsed[target]) for p in FORBIDDEN.values()), f"ALLOWED[{target!r}] is not needed"


def test_environment_and_image_targets_are_cli_aliases():
    parsed = recipes()
    missing = sorted(CLI_ALIASES - set(parsed))
    assert not missing, f"targets missing from the Makefile: {missing}"
    not_cli = sorted(
        t for t in CLI_ALIASES if "esmeralda " not in parsed[t] and "$(ESM) " not in parsed[t] and t != "build-images"
    )
    assert not not_cli, f"these targets must call the esmeralda CLI: {not_cli}"


def test_guard_patterns_catch_direct_calls():
    pattern = FORBIDDEN["terragrunt"]
    assert pattern.search("\t@cd x && terragrunt --non-interactive apply")
    assert pattern.search("\tENGINE=$$(terragrunt output -raw engine_id)")
    assert not pattern.search('\t@find . -type d -name ".terragrunt-cache" -exec rm -rf {} +')
    assert not pattern.search("export PATH := $(HOME)/.terragrunt/bin:$(PATH)")
    assert FORBIDDEN["gcloud builds"].search("\tgcloud builds submit apps/x --config=y")
    assert FORBIDDEN["gcloud builds"].search("\tgcloud --quiet builds submit apps/x")
    assert FORBIDDEN["gcloud run deploy"].search("\tgcloud run deploy svc --image=x")
    assert not FORBIDDEN["gcloud run deploy"].search("\tgcloud run services describe svc")
