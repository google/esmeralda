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
SECTIONS = {"gateway_contract": "The Gateway Contract", "gateways_block": "The `gateways` Block"}
SECTION_LIMIT = 2500


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


def describe_contracts(root: Path) -> dict[str, Any]:
    doc = root / GATEWAY_DOC
    markdown = doc.read_text() if doc.is_file() else ""
    result: dict[str, Any] = {"source": GATEWAY_DOC.as_posix() if markdown else None}
    for key, title in SECTIONS.items():
        result[key] = section(markdown, title) or None
    result["envs"] = {env: repo.gateways_block(root, env) for env in repo.environments(root)}
    result["gateway_units"] = {
        env: [u.rsplit("/", 1)[-1] for u in repo.units(root, env) if u.startswith("layer-5-workloads/gateways/")]
        for env in repo.environments(root)
    }
    return result
