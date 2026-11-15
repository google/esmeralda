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

"""Guards against perpetual Terraform diffs on Agent Runtime (Reasoning Engine) resources.

For A2A agents, Agent Runtime fills ``spec.agent_card`` server side (from the ``a2a_agent_card`` in
``class_methods``, with the url rewritten to the engine's own A2A endpoint). The provider marks the
attribute optional but not computed, so a module that leaves it unset plans an in-place update on
every run unless it ignores exactly that attribute. These tests keep that fix in place and keep the
ignore list narrow, so it can never hide a real image, env or class_methods change.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENT_MODULES = REPO_ROOT / "infrastructure/modules/5-workloads/agents"
A2A_MAIN = AGENT_MODULES / "a2a-agent/main.tf"

RESOURCE_RE = re.compile(r'^resource\s+"google_vertex_ai_reasoning_engine"\s+"(\w+)"\s*\{', re.MULTILINE)
# The list may hold index brackets (spec[0].x), so match up to the last `]` before a newline.
IGNORE_RE = re.compile(r"ignore_changes\s*=\s*\[(.*?)\]\s*$", re.DOTALL | re.MULTILINE)

# The only attributes a Reasoning Engine resource may ignore.
ALLOWED_IGNORES = {"spec[0].agent_card"}


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _blocks(path: pathlib.Path) -> dict[str, str]:
    """The body of every google_vertex_ai_reasoning_engine resource in ``path``, by name."""
    text = _strip_comments(path.read_text())
    blocks = {}
    for match in RESOURCE_RE.finditer(text):
        depth, i = 1, match.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        blocks[match.group(1)] = text[match.end() : i - 1]
    return blocks


def _ignores(body: str) -> set[str]:
    found: set[str] = set()
    for match in IGNORE_RE.finditer(body):
        found |= {item.strip() for item in match.group(1).split(",") if item.strip()}
    return found


def test_a2a_agent_ignores_server_side_agent_card() -> None:
    blocks = _blocks(A2A_MAIN)
    assert "agent" in blocks, f"{A2A_MAIN}: google_vertex_ai_reasoning_engine.agent not found"
    body = blocks["agent"]
    assert "spec[0].agent_card" in _ignores(body), (
        "a2a-agent must keep lifecycle { ignore_changes = [spec[0].agent_card] }: Agent Runtime fills "
        "spec.agent_card server side, and without it every plan shows an in-place update to null"
    )
    # The card reaches the API through class_methods, which stays tracked.
    assert "a2a_agent_card" in body, "a2a-agent must send the card through class_methods (a2a_agent_card)"
    assert not re.search(r"^\s*agent_card\s*=", body, re.MULTILINE), (
        "spec.agent_card is ignored, so setting it in config would be silently dropped after create; "
        "change the card in agent.yaml (class_methods) instead, or remove the ignore"
    )


def test_reasoning_engine_ignore_changes_stay_narrow() -> None:
    modules = sorted(AGENT_MODULES.glob("*/*.tf"))
    assert modules, f"no Terraform files under {AGENT_MODULES}"
    for path in modules:
        for name, body in _blocks(path).items():
            extra = _ignores(body) - ALLOWED_IGNORES
            assert not extra, (
                f"{path.relative_to(REPO_ROOT)}: google_vertex_ai_reasoning_engine.{name} ignores {sorted(extra)}; "
                f"only {sorted(ALLOWED_IGNORES)} may be ignored, so image, env and class_methods changes stay visible"
            )
            assert not re.search(r"ignore_changes\s*=\s*all\b", body), (
                f"{path.relative_to(REPO_ROOT)}: google_vertex_ai_reasoning_engine.{name} must not ignore all changes"
            )
