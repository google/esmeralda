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

"""The CLI examples in the README and the docs stay runnable.

Every ``esmeralda ...`` command in a ``bash`` block of ``README.md`` or ``docs/src`` must parse with
the real argparse definitions, every ``make <target>`` must exist in the Makefile, and the README
section that ``esmeralda doctor`` points to must exist.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

from esmeralda import cli
from esmeralda.ops import repo

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"
MAKEFILE = REPO_ROOT / "Makefile"
DOCS = [README, *sorted((REPO_ROOT / "docs" / "src").rglob("*.md"))] if README.is_file() else []

pytestmark = pytest.mark.skipif(not README.is_file(), reason="repository README not available")

_BASH_BLOCK_RE = re.compile(r"^\s*```(?:bash|sh|shell)\n(.*?)^\s*```", re.MULTILINE | re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"<[^<>\s]+>")
_SYNOPSIS_CHARS = ("[", "|", "...", "…")  # usage synopses such as `esmeralda doctor [--env dev|prd]`


def _commands(path: Path) -> list[str]:
    """Shell commands of the file's bash blocks, continuation lines joined, comments dropped."""
    commands: list[str] = []
    for block in _BASH_BLOCK_RE.findall(path.read_text()):
        joined = re.sub(r"\\\n\s*", " ", block)
        for line in joined.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                commands.append(line)
    return commands


def _argv(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        return []
    return [_PLACEHOLDER_RE.sub("placeholder", t) for t in tokens]


def _esmeralda_invocations(paths: list[Path]) -> list[tuple[str, list[str]]]:
    """(file, arguments after ``esmeralda``) for every example command that runs the CLI."""
    found = []
    for path in paths:
        for command in _commands(path):
            if any(c in command for c in _SYNOPSIS_CHARS):
                continue
            argv = _argv(command)
            # the CLI itself, not a `uv run --package esmeralda ...` package name
            at = [i for i, t in enumerate(argv) if t == "esmeralda" and (i == 0 or not argv[i - 1].startswith("-"))]
            if at and (at[0] == 0 or argv[:2] == ["uv", "run"]):
                found.append((path.relative_to(REPO_ROOT).as_posix(), argv[at[0] + 1 :]))
    return found


def test_readme_has_esmeralda_examples():
    invocations = [args for _, args in _esmeralda_invocations([README])]
    assert invocations, "no esmeralda commands found in README bash blocks"
    assert any(args and args[0] == "query" for args in invocations), "README Quick Start lost its query example"


@pytest.mark.parametrize(
    ("doc", "args"),
    _esmeralda_invocations(DOCS),
    ids=lambda v: v if isinstance(v, str) else " ".join(v),
)
def test_doc_esmeralda_commands_parse(doc, args):
    """Each documented example is accepted by the real CLI parser (subcommand, flags and positionals)."""
    try:
        cli._parser().parse_args(args)
    except SystemExit as exc:  # argparse exits on unknown flags or missing arguments (and 0 on --help)
        if exc.code:
            pytest.fail(f"{doc}: command does not parse: esmeralda {' '.join(args)} (exit {exc.code})")


def test_doc_make_targets_exist():
    targets = set(re.findall(r"^([A-Za-z0-9_-]+):", MAKEFILE.read_text(), re.MULTILINE))
    used = {
        (path.relative_to(REPO_ROOT).as_posix(), argv[1])
        for path in DOCS
        for argv in map(_argv, _commands(path))
        if len(argv) > 1 and argv[0] == "make" and not argv[1].startswith("-")
    }
    assert any(doc == "README.md" for doc, _ in used), "no make commands found in README bash blocks"
    missing = sorted((doc, t) for doc, t in used if t not in targets)
    assert not missing, f"docs use make targets missing from the Makefile: {missing}"


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", heading.strip().lower()).replace(" ", "-")


def test_doctor_install_hints_point_to_existing_readme_section():
    text = README.read_text()
    anchors = {_slug(h) for h in re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)}
    anchors |= set(re.findall(r'<a id="([^"]+)"', text))
    for tool, hint in repo.INSTALL_HINTS.items():
        for anchor in re.findall(r"README\.md#([\w-]+)", hint):
            assert anchor in anchors, f"INSTALL_HINTS[{tool!r}] points to missing README section #{anchor}"
        assert "Prerequisites" not in hint or "README.md#prerequisites" in hint, hint
    for tool in ("gcloud", "terraform", "terragrunt", "uv", "jq"):
        assert re.search(rf"`{tool}`", _prerequisites_section(text)), f"README Prerequisites does not list {tool}"


def _prerequisites_section(text: str) -> str:
    match = re.search(r"^(#{2,4})\s+Prerequisites\s*$(.*?)(?=^#{2,4}\s)", text, re.MULTILINE | re.DOTALL)
    assert match, "README has no Prerequisites section"
    return match.group(2)
