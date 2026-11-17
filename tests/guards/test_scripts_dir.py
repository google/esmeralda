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

"""``scripts/`` only shrinks: every file in it is on an allowlist, with the reason it is still there.

New logic goes into the ``esmeralda`` CLI (``packages/esmeralda/src/esmeralda/ops/``, with a thin
Make alias if needed) and static checks into ``tests/guards/``. The only code that has to stay
shell is what runs on the test VM over IAP SSH (no checkout or virtual environment there). When a
script is ported, delete it and its entry here.

The Makefile, the git hooks and the CLI may only call scripts that are on the allowlist and exist.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
README = SCRIPTS_DIR / "README.md"

# path under scripts/ -> why it is still a script (and what it becomes).
ALLOWED: dict[str, str] = {
    "README.md": "what each script is for and who calls it",
    "preflight.sh": "make preflight / bootstrap; to be replaced by esmeralda doctor",
    "vm/gateway_probes.sh": "gateway probes of esmeralda verify; runs ON the test VM over IAP SSH (no checkout or venv there)",
    "vm/partner_load.sh": "make load-test-partner; installs and runs Locust ON the test VM over IAP SSH",
}

# Entries that may be absent: files another open change adds or deletes. Drop them from here (and
# ALLOWED, if deleted) once that change has landed.
OPTIONAL: set[str] = set()

# `bash scripts/x.sh`, `./scripts/x.sh`, `scripts/x.sh` (not `.../_shared/scripts/...`).
SCRIPT_REF_RE = re.compile(r"(?<![\w./-])(?:\./)?scripts/([\w./-]*[\w])")

# First-column file names in the README table: | `name` | ... |
README_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|")


def _tracked_scripts() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", "scripts"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return {line.removeprefix("scripts/") for line in out.splitlines() if line}


def _existing_scripts() -> set[str]:
    return {
        str(p.relative_to(SCRIPTS_DIR))
        for p in SCRIPTS_DIR.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def _references() -> dict[str, set[str]]:
    """``script -> {where}`` for every scripts/ path called from the Makefile, the git hooks or the CLI."""
    sources: list[tuple[str, str]] = []
    makefile = (REPO_ROOT / "Makefile").read_text()
    sources.append(("Makefile recipe", "\n".join(l for l in makefile.splitlines() if l.startswith("\t"))))
    for hook in sorted((REPO_ROOT / ".githooks").glob("*")):
        if hook.is_file():
            sources.append((f".githooks/{hook.name}", hook.read_text()))
    for module in sorted((REPO_ROOT / "packages/esmeralda/src").rglob("*.py")):
        sources.append((str(module.relative_to(REPO_ROOT)), module.read_text()))
    refs: dict[str, set[str]] = {}
    for where, text in sources:
        for match in SCRIPT_REF_RE.finditer(text):
            refs.setdefault(match.group(1), set()).add(where)
    return refs


def test_every_script_is_allowlisted():
    unlisted = sorted((_tracked_scripts() | _existing_scripts()) - set(ALLOWED))
    assert not unlisted, (
        f"files in scripts/ that are not on the allowlist: {unlisted}. New logic goes into the esmeralda CLI "
        "(packages/esmeralda/src/esmeralda/ops/) and static checks into tests/guards/; only code that must run "
        "on the test VM stays a script. If it really has to be a script, add it to ALLOWED with the reason."
    )


def test_allowlist_has_reasons_and_no_stale_entries():
    existing = _existing_scripts()
    for name, reason in ALLOWED.items():
        assert reason.strip(), f"ALLOWED[{name!r}] needs a reason"
    stale = sorted(set(ALLOWED) - OPTIONAL - existing)
    assert not stale, f"ALLOWED entries for files that no longer exist (delete them): {stale}"
    assert OPTIONAL <= set(ALLOWED), f"OPTIONAL entries missing from ALLOWED: {sorted(OPTIONAL - set(ALLOWED))}"


def _is_allowed_ref(name: str, existing: set[str]) -> bool:
    """A reference is fine if it names an allowlisted file that exists, or a folder holding one."""
    if name in ALLOWED and name in existing:
        return True
    return any(f.startswith(name.rstrip("/") + "/") for f in existing if f in ALLOWED)


def test_callers_only_use_allowlisted_existing_scripts():
    existing = _existing_scripts()
    bad = sorted(
        f"{where}: scripts/{name}"
        for name, wheres in _references().items()
        for where in wheres
        if not _is_allowed_ref(name, existing)
    )
    assert not bad, (
        f"these callers use a script that is missing or not allowlisted: {bad}. "
        "Make targets should be esmeralda CLI aliases (see tests/guards/test_makefile_cli.py)."
    )


def test_readme_lists_exactly_the_scripts():
    if not README.exists():
        return
    listed = {m.group(1) for line in README.read_text().splitlines() if (m := README_ROW_RE.match(line))}
    existing = _existing_scripts() - {"README.md"}
    missing = sorted(listed - existing)
    assert not missing, f"scripts/README.md lists files that do not exist: {missing}"
    undocumented = sorted(existing - listed)
    assert not undocumented, f"scripts/README.md does not list: {undocumented}"


def test_reference_pattern():
    assert [m.group(1) for m in SCRIPT_REF_RE.finditer("\t@bash scripts/uv_lock.sh $(PKG)")] == ["uv_lock.sh"]
    assert [m.group(1) for m in SCRIPT_REF_RE.finditer("\t@./scripts/preflight.sh")] == ["preflight.sh"]
    assert [m.group(1) for m in SCRIPT_REF_RE.finditer("exec scripts/check_lock.sh")] == ["check_lock.sh"]
    assert not SCRIPT_REF_RE.search("infrastructure/modules/_shared/scripts/grant_iap_egress.sh")
    assert not SCRIPT_REF_RE.search("apps/agents/x/scripts/chaos_telemetry_test.py")
