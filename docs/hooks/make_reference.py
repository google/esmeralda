"""MkDocs hook: generate the "Make Targets" reference page from the Makefile.

Every documented target in the Makefile looks like ``target: [deps] ## description``, and sections
are introduced by a banner::

    # ==============================================================================
    # Layer 5: workloads (...)
    # ==============================================================================

The page is generated at build time, so it can never drift from the Makefile.
"""

from __future__ import annotations

import os
import re

from mkdocs.structure.files import File

PAGE = "reference/make-targets.md"
_TARGET = re.compile(r"^([A-Za-z0-9_.-]+):[^=]*?##\s*(.+)$")
_BANNER = re.compile(r"^#\s*={5,}\s*$")


def _parse(makefile: str) -> list[tuple[str, list[tuple[str, str]]]]:
    sections: list[tuple[str, list[tuple[str, str]]]] = [("Setup, local development and tests", [])]
    lines = makefile.splitlines()
    for i, line in enumerate(lines):
        if _BANNER.match(line) and i + 1 < len(lines) and not _BANNER.match(lines[i + 1]):
            title = lines[i + 1].lstrip("#").strip()
            # The banner closes within a few comment lines (some banners have a second line).
            closes = any(_BANNER.match(lines[j]) for j in range(i + 2, min(i + 5, len(lines))))
            if title and closes:
                sections.append((title, []))
            continue
        match = _TARGET.match(line)
        if match:
            sections[-1][1].append((match.group(1), match.group(2).strip()))
    return [(title, targets) for title, targets in sections if targets]


def _render(sections: list[tuple[str, list[tuple[str, str]]]]) -> str:
    out = [
        "# Make Targets",
        "",
        "Every workflow in Esmeralda runs through the root [`Makefile`](https://github.com/google/esmeralda/blob/main/Makefile). "
        "Run `make help` for the same list in your terminal. Most targets take `ENV=<env>` "
        "(default `dev`).",
        "",
        "!!! note",
        "",
        "    This page is generated from the `## ...` comments in the Makefile at build time.",
        "",
    ]
    for title, targets in sections:
        out += [f"## {title}", "", "| Target | What it does |", "| :--- | :--- |"]
        for name, desc in targets:
            desc = desc.replace("|", "\\|")
            out.append(f"| `make {name}` | {desc} |")
        out.append("")
    return "\n".join(out)


def on_files(files, config):
    makefile = os.path.join(os.path.dirname(config["config_file_path"]), "..", "Makefile")
    with open(makefile, encoding="utf-8") as fh:
        content = _render(_parse(fh.read()))
    files.append(File.generated(config, PAGE, content=content))
    return files
