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

"""The gateway adapter contract and the ``gateways`` block, read from the docs and env.yaml files.

The docs are the source of truth (``docs/src/2-workloads-and-catalog/01-ingress-gateways.md``);
this module only extracts the two sections and each environment's current ``gateways`` block.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from esmeralda.ops import repo

GATEWAY_DOC = Path("docs/src/2-workloads-and-catalog/01-ingress-gateways.md")
SECTIONS = {
    "gateway_contract": "The Gateway Contract",
    "gateways_block": "The `gateways` Block",
    "entrypoint": "The Entry Point",
}
VALID_SECTIONS = ("all", "entrypoint", "gateway_adapter", "agent_card", "env_yaml")
SECTION_LIMIT = 2500

_DEFAULT_AGENT_CARD_CONTRACT = (
    "A2A agents declare `framework: a2a` and an `agent_card` mapping in `apps/agents/<name>/agent.yaml` "
    "(`name`, `description`, `version`, `url`, `capabilities`, `defaultInputModes`, `defaultOutputModes`, `skills`). "
    "`esmeralda serve` serves the card at `/v1/card` and `/.well-known/agent-card.json`. "
    "Multi-turn conversation context is persisted in VertexAiSessionService keyed by `context_id`; "
    "A2A agents using the default in-memory task store (no `a2a_task_store_builder`) must run a single worker "
    "with `resources.max_instances: 1`."
)


def section(markdown: str, title: str) -> str:
    """The body of the first heading containing ``title``, up to the next heading of any level ≤ its own."""
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(#+)\s+(.*)$", line)
        if match and title in match.group(2):
            level = len(match.group(1))
            body = []
            for following in lines[index + 1 :]:
                heading = re.match(r"^(#+)\s", following)
                if heading and len(heading.group(1)) <= level:
                    break
                body.append(following)
            return _compact("\n".join(body))
    return ""


def _compact(text: str) -> str:
    text = re.sub(r"\]\([^)]*\)", "]", text)  # drop link targets: relative paths are noise to an agent
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text if len(text) <= SECTION_LIMIT else text[: SECTION_LIMIT - 1] + "…"


def _agent_cards(root: Path) -> dict[str, Any]:
    agents_dir = root / "apps" / "agents"
    cards: dict[str, Any] = {}
    if not agents_dir.is_dir():
        return cards
    for agent_yaml in sorted(agents_dir.glob("*/agent.yaml")):
        text = agent_yaml.read_text(errors="replace")
        if "agent_card:" in text or "framework: a2a" in text:
            cards[agent_yaml.parent.name] = f"apps/agents/{agent_yaml.parent.name}/agent.yaml"
    return cards


def _a2a_task_store_warnings(root: Path) -> list[str]:
    agents_dir = root / "apps" / "agents"
    warnings: list[str] = []
    if not agents_dir.is_dir():
        return warnings
    for agent_yaml in sorted(agents_dir.glob("*/agent.yaml")):
        text = agent_yaml.read_text(errors="replace")
        if "framework: a2a" not in text and "agent_card:" not in text:
            continue
        max_match = re.search(r"^\s*max_instances\s*:\s*(\d+)", text, re.MULTILINE)
        max_inst = int(max_match.group(1)) if max_match else 1
        init_py = agent_yaml.parent / "agent" / "__init__.py"
        init_text = init_py.read_text(errors="replace") if init_py.is_file() else ""
        if max_inst > 1 and "a2a_task_store_builder" not in init_text:
            warnings.append(
                f"{agent_yaml.parent.name}: framework: a2a with max_instances={max_inst} and no "
                "a2a_task_store_builder (in-memory task store requires a single worker with max_instances: 1)"
            )
    return warnings


def describe_contracts(root: Path, *, section_name: str = "all") -> dict[str, Any]:
    if section_name not in VALID_SECTIONS:
        raise repo.OpsError(f"Unknown contract section '{section_name}'. Choose from: {', '.join(VALID_SECTIONS)}")
    doc = root / GATEWAY_DOC
    markdown = doc.read_text() if doc.is_file() else ""
    gw_contract = section(markdown, SECTIONS["gateway_contract"]) or None
    gw_block = section(markdown, SECTIONS["gateways_block"]) or None
    ep_contract = section(markdown, SECTIONS["entrypoint"]) or None
    envs = {env: repo.gateways_block(root, env) for env in repo.environments(root)}
    gw_units = {
        env: [u.rsplit("/", 1)[-1] for u in repo.units(root, env) if u.startswith("layer-5-workloads/gateways/")]
        for env in repo.environments(root)
    }
    agent_card_info: dict[str, Any] = {
        "contract": _DEFAULT_AGENT_CARD_CONTRACT,
        "a2a_agents": _agent_cards(root),
    }
    if a2a_warnings := _a2a_task_store_warnings(root):
        agent_card_info["warnings"] = a2a_warnings

    if section_name == "entrypoint":
        return {
            "section": "entrypoint",
            "source": GATEWAY_DOC.as_posix() if markdown else None,
            "entrypoint": ep_contract,
        }
    if section_name == "gateway_adapter":
        return {
            "section": "gateway_adapter",
            "source": GATEWAY_DOC.as_posix() if markdown else None,
            "gateway_adapter": gw_contract,
            "gateway_units": gw_units,
        }
    if section_name == "env_yaml":
        return {
            "section": "env_yaml",
            "source": GATEWAY_DOC.as_posix() if markdown else None,
            "env_yaml": gw_block,
            "envs": envs,
        }
    if section_name == "agent_card":
        return {"section": "agent_card", "agent_card": agent_card_info}

    result: dict[str, Any] = {
        "source": GATEWAY_DOC.as_posix() if markdown else None,
        "gateway_contract": gw_contract,
        "gateways_block": gw_block,
        "entrypoint": ep_contract,
        "gateway_adapter": gw_contract,
        "env_yaml": gw_block,
        "agent_card": agent_card_info,
        "envs": envs,
        "gateway_units": gw_units,
    }
    return result
