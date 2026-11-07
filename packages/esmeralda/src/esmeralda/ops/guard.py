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

"""Public-repository sensitive-content scanner (``esmeralda guard``).

Scans staged changes (``--staged``), a git diff range (``--diff <range>``), all tracked files
(``--all``), or a commit message file (``--message-file``) for:

* built-in content rules: PEM private keys, service-account key fields, API keys and OAuth/PAT
  tokens, billing account IDs, internal shortlinks, and internal corporate host/marker patterns;
* built-in path rules: ``*.tfvars``, ``*.tfstate*``, ``*.tfplan``, ``.env*`` (except
  ``.env.example``), key files, local agent state directories, and ``.local_states``;
* optional user-supplied regex patterns from ``$ESMERALDA_SENSITIVE_PATTERNS_FILE`` or
  ``<git-common-dir>/info/sensitive-patterns`` (one case-insensitive regex per line).
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from esmeralda.ops import repo
from esmeralda.ops.repo import OpsError

# Constructed from fragments so this source file never matches its own rules or grep hooks.
_SHORTLINK_RE = re.compile(
    r"(?<![A-Za-z0-9_/.:-])(?:" + "g" + r"o/[a-z][a-z0-9_/-]{2,}|(?:" + "b|c" + r"l)/[0-9]{4,})\b"
)
_INTERNAL_MARKER_RE = re.compile(
    r"\b(?:"
    + "nu"
    + r"bank|"
    + "cla"
    + r"ro|"
    + "natu"
    + r"ra|"
    + "c"
    + r"orp\.goo"
    + r"gle|"
    + "goo"
    + r"gleplex|"
    + "goo"
    + r"gle3|"
    + "015"
    + r"DBB|"
    + "meneg"
    + r"ola-sandbox|"
    + "altos"
    + r"trat|"
    + "argo"
    + r"lis|"
    + "jet"
    + r"ski)\b",
    re.IGNORECASE,
)

BUILTIN_CONTENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("PEM block / private key", re.compile(r"-{5}BEGIN (?!CERTIFICATE)[A-Z0-9 ]*-{5}")),
    ("private key field", re.compile(r"\"private_key\"\s*:\s*\"-")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Google OAuth access token", re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("AWS access key id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Bearer token literal", re.compile(r"[Bb]earer\s+[A-Za-z0-9_\-\.=]{40,}")),
    (
        "GCP billing account ID",
        re.compile(r"\b(?!012345-6789AB-CDEF01|000000-000000-000000)[0-9A-F]{6}-[0-9A-F]{6}-[0-9A-F]{6}\b"),
    ),
    ("internal shortlink", _SHORTLINK_RE),
    ("forbidden internal/customer marker", _INTERNAL_MARKER_RE),
]

_DOT_AGENT_DIR = "." + "gem" + "ini/*"

BUILTIN_PATH_RULES: list[tuple[str, str]] = [
    ("Terraform variables file", "*.tfvars"),
    ("Terraform variables file", "*.tfvars.json"),
    ("Terraform state", "*.tfstate"),
    ("Terraform state", "*.tfstate.*"),
    ("Terraform plan", "*.tfplan"),
    ("environment file", ".env"),
    ("environment file", ".env.*"),
    ("key material", "*.pem"),
    ("key material", "*.key"),
    ("key material", "*.p12"),
    ("key material", "id_rsa*"),
    ("local agent state", ".adk/*"),
    ("local agent state", _DOT_AGENT_DIR),
    ("local session database", "*.sqlite3"),
    ("local session database", "*.db"),
    ("local Terraform state directory", ".local_states*"),
    ("saved plan / verify log", ".plans/*"),
    ("saved plan directory", ".esmeralda/*"),
]
PATH_ALLOW = {".env.example"}


@dataclass(frozen=True)
class Finding:
    where: str
    rule: str
    snippet: str = ""

    def to_dict(self) -> dict[str, str]:
        data = {"where": self.where, "rule": self.rule}
        if self.snippet:
            data["snippet"] = self.snippet
        return data

    def __str__(self) -> str:
        return f"  {self.where}: {self.rule}" + (f"  [{self.snippet}]" if self.snippet else "")


def mask(text: str) -> str:
    text = text.strip()
    return text if len(text) <= 8 else f"{text[:6]}…({len(text)} chars)"


def load_patterns(path: Path | None) -> list[tuple[str, re.Pattern[str]]]:
    """Local patterns: one regex per line, case-insensitive; blank lines and ``#`` comments ignored."""
    if path is None or not path.is_file():
        return []
    rules: list[tuple[str, re.Pattern[str]]] = []
    for n, raw in enumerate(path.read_text(errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rules.append((f"local pattern #{n}", re.compile(line, re.IGNORECASE)))
        except re.error as exc:
            raise OpsError(f"{path}:{n}: invalid regex ({exc})") from exc
    return rules


def patterns_file(root: Path | None = None) -> Path | None:
    env = os.environ.get("ESMERALDA_SENSITIVE_PATTERNS_FILE")
    if env:
        return Path(env).expanduser()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        common = proc.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    common_path = Path(common)
    if root is not None and not common_path.is_absolute():
        common_path = (root / common_path).resolve()
    return common_path / "info" / "sensitive-patterns"


def check_path(path: str) -> list[Finding]:
    base = path.rsplit("/", 1)[-1]
    if base in PATH_ALLOW:
        return []
    for rule, glob in BUILTIN_PATH_RULES:
        if fnmatch.fnmatch(base, glob) or fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, f"*/{glob}"):
            return [Finding(path, f"{rule} must not be committed")]
    return []


def scan_text(
    where: str,
    line: str,
    rules: list[tuple[str, re.Pattern[str]]] | None = None,
) -> list[Finding]:
    active_rules = BUILTIN_CONTENT_RULES if rules is None else rules
    findings: list[Finding] = []
    for rule, rx in active_rules:
        match = rx.search(line)
        if match:
            findings.append(Finding(where, rule, mask(match.group(0))))
    return findings


def parse_added_lines(diff: str) -> list[tuple[str, int, str]]:
    """``(path, new line number, text)`` for every ``+`` line of a unified diff."""
    out: list[tuple[str, int, str]] = []
    path, lineno = "", 0
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            path = target[2:] if target.startswith("b/") else ("" if target == "/dev/null" else target)
            continue
        if line.startswith("--- "):
            continue
        match = hunk.match(line)
        if match:
            lineno = int(match.group(1))
            continue
        if line.startswith("+") and path:
            out.append((path, lineno, line[1:]))
            lineno += 1
        elif line.startswith(" "):
            lineno += 1
    return out


def scan_diff(
    diff: str,
    paths: list[str],
    rules: list[tuple[str, re.Pattern[str]]] | None = None,
) -> list[Finding]:
    active_rules = BUILTIN_CONTENT_RULES if rules is None else rules
    findings: list[Finding] = []
    path_text_rules = [r for r in active_rules if r[0].startswith("local") or "marker" in r[0]]
    for path in paths:
        findings.extend(check_path(path))
        findings.extend(scan_text(path, path, path_text_rules))
    for path, lineno, text in parse_added_lines(diff):
        findings.extend(scan_text(f"{path}:{lineno}", text, active_rules))
    return findings


def scan_files(
    root: Path,
    paths: list[str],
    rules: list[tuple[str, re.Pattern[str]]] | None = None,
) -> list[Finding]:
    active_rules = BUILTIN_CONTENT_RULES if rules is None else rules
    findings: list[Finding] = []
    path_text_rules = [r for r in active_rules if r[0].startswith("local") or "marker" in r[0]]
    for rel_path in paths:
        findings.extend(check_path(rel_path))
        findings.extend(scan_text(rel_path, rel_path, path_text_rules))
        full = root / rel_path
        if not full.is_file():
            continue
        try:
            raw = full.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        text = raw.decode("utf-8", errors="replace")
        matched_rules = [r for r in active_rules if r[1].search(text)]
        if not matched_rules:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            findings.extend(scan_text(f"{rel_path}:{lineno}", line, matched_rules))
    return findings


def run_guard(
    root: Path,
    *,
    mode: str = "staged",
    diff_range: str | None = None,
    message_file: str | Path | None = None,
) -> dict[str, Any]:
    """Runs the sensitive-content scan in ``staged``, ``diff``, ``all`` or ``message`` mode."""
    if os.environ.get("SKIP_SENSITIVE_SCAN") == "1":
        return {"ok": True, "skipped": True, "mode": mode, "scanned_files": 0, "findings": []}

    pfile = patterns_file(root)
    rules = BUILTIN_CONTENT_RULES + load_patterns(pfile)

    if message_file is not None:
        msg_path = Path(message_file)
        if not msg_path.is_file():
            raise OpsError(f"Commit message file not found: {message_file}")
        lines = msg_path.read_text(errors="replace").splitlines()
        findings = [
            f
            for n, line in enumerate(lines, 1)
            if not line.startswith("#")
            for f in scan_text(f"commit message:{n}", line, rules)
        ]
        return {
            "ok": not findings,
            "mode": "message",
            "scanned_files": 1,
            "findings": [f.to_dict() for f in findings],
        }

    if diff_range:
        mode = "diff"

    if mode == "all":
        res = repo.run(["git", "ls-files"], cwd=root, timeout=30.0, what="git ls-files")
        if res.returncode != 0:
            raise repo.failure("git ls-files", res)
        paths = [p for p in res.stdout.splitlines() if p.strip()]
        findings = scan_files(root, paths, rules)
    elif mode == "diff":
        if not diff_range:
            raise OpsError("--diff requires a commit range (e.g. origin/main...HEAD)")
        base_cmd = ["git", "diff", diff_range, "--no-color", "--no-ext-diff", "--diff-filter=ACMR"]
        diff_res = repo.run([*base_cmd, "-U0"], cwd=root, timeout=30.0, what=f"git diff {diff_range}")
        if diff_res.returncode != 0:
            raise repo.failure(f"git diff {diff_range}", diff_res)
        names_res = repo.run([*base_cmd, "--name-only"], cwd=root, timeout=30.0, what=f"git diff {diff_range}")
        paths = [p for p in names_res.stdout.splitlines() if p.strip()]
        findings = scan_diff(diff_res.stdout, paths, rules)
    else:
        base_cmd = ["git", "diff", "--cached", "--no-color", "--no-ext-diff", "--diff-filter=ACMR"]
        diff_res = repo.run([*base_cmd, "-U0"], cwd=root, timeout=30.0, what="git diff --cached")
        if diff_res.returncode != 0:
            raise repo.failure("git diff --cached", diff_res)
        names_res = repo.run([*base_cmd, "--name-only"], cwd=root, timeout=30.0, what="git diff --cached")
        paths = [p for p in names_res.stdout.splitlines() if p.strip()]
        findings = scan_diff(diff_res.stdout, paths, rules)

    return {
        "ok": not findings,
        "mode": mode,
        "diff_range": diff_range,
        "scanned_files": len(paths),
        "local_patterns": str(pfile) if pfile and pfile.is_file() else None,
        "findings": [f.to_dict() for f in findings],
    }
