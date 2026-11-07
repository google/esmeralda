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
"""Blocks commits that would publish secrets or private material (the git pre-commit hook).

Scans the lines ADDED by the staged diff (and, with --message-file, a commit message) for:
  * built-in, generic rules: PEM blocks / private keys, common token formats, and files that must
    never be committed (*.tfvars, Terraform state and plans, .env files, keys, local agent state);
  * your own patterns, one Python regex per line (case-insensitive, `#` comments), from
    $ESMERALDA_SENSITIVE_PATTERNS_FILE or <git common dir>/info/sensitive-patterns. That file is
    local and untracked: put organisation, customer or environment names there, never in this repo.

Bypass once with SKIP_SENSITIVE_SCAN=1 (e.g. `SKIP_SENSITIVE_SCAN=1 git commit ...`).
Exit 0 clean, 1 findings (or error).
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# (name, regex) for added content. Written so this file does not match itself.
BUILTIN_CONTENT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("PEM block / private key", re.compile(r"-{5}BEGIN[A-Z0-9 ]*-{5}")),
    ("private key field", re.compile(r"\"private_key\"\s*:\s*\"-")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Google OAuth access token", re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("AWS access key id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Bearer token literal", re.compile(r"[Bb]earer\s+[A-Za-z0-9_\-\.=]{40,}")),
]

# Staged paths that must never be committed (matched against the path and its basename).
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
    ("local agent state", ".gemini/*"),
    ("local session database", "*.sqlite3"),
    ("local session database", "*.db"),
    ("local Terraform state directory", ".local_states*"),
    ("saved plan / verify log (make plan)", ".plans/*"),
]
PATH_ALLOW = {".env.example"}


@dataclass(frozen=True)
class Finding:
    where: str
    rule: str
    snippet: str = ""

    def __str__(self) -> str:
        return f"  {self.where}: {self.rule}" + (f"  [{self.snippet}]" if self.snippet else "")


def mask(text: str) -> str:
    text = text.strip()
    return text if len(text) <= 8 else f"{text[:6]}…({len(text)} chars)"


def load_patterns(path: Path | None) -> list[tuple[str, re.Pattern[str]]]:
    """Local patterns: one regex per line, case-insensitive; blank lines and # comments ignored."""
    if path is None or not path.is_file():
        return []
    rules = []
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rules.append((f"local pattern #{n}", re.compile(line, re.IGNORECASE)))
        except re.error as exc:
            raise SystemExit(f"❌ {path}:{n}: invalid regex ({exc})") from exc
    return rules


def patterns_file() -> Path | None:
    env = os.environ.get("ESMERALDA_SENSITIVE_PATTERNS_FILE")
    if env:
        return Path(env).expanduser()
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(common) / "info" / "sensitive-patterns"


def check_path(path: str) -> list[Finding]:
    base = path.rsplit("/", 1)[-1]
    if base in PATH_ALLOW:
        return []
    for rule, glob in BUILTIN_PATH_RULES:
        if fnmatch.fnmatch(base, glob) or fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, f"*/{glob}"):
            return [Finding(path, f"{rule} must not be committed")]
    return []


def scan_text(where: str, line: str, rules: list[tuple[str, re.Pattern[str]]]) -> list[Finding]:
    findings = []
    for rule, rx in rules:
        m = rx.search(line)
        if m:
            findings.append(Finding(where, rule, mask(m.group(0))))
    return findings


def parse_added_lines(diff: str) -> list[tuple[str, int, str]]:
    """(path, new line number, text) for every '+' line of a unified diff (-U0 or more)."""
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
        m = hunk.match(line)
        if m:
            lineno = int(m.group(1))
            continue
        if line.startswith("+") and path:
            out.append((path, lineno, line[1:]))
            lineno += 1
        elif line.startswith(" "):
            lineno += 1
    return out


def scan_diff(diff: str, staged_paths: list[str], rules: list[tuple[str, re.Pattern[str]]]) -> list[Finding]:
    findings: list[Finding] = []
    for path in staged_paths:
        findings += check_path(path)
        findings += scan_text(path, path, [r for r in rules if r[0].startswith("local")])  # names in paths
    for path, lineno, text in parse_added_lines(diff):
        findings += scan_text(f"{path}:{lineno}", text, rules)
    return findings


def staged() -> tuple[str, list[str]]:
    git = ["git", "diff", "--cached", "--no-color", "--no-ext-diff", "--diff-filter=ACMR"]
    diff = subprocess.run([*git, "-U0"], capture_output=True, text=True, check=True, errors="replace").stdout
    names = subprocess.run([*git, "--name-only"], capture_output=True, text=True, check=True).stdout.split("\n")
    return diff, [n for n in names if n]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--message-file", help="scan a commit message file instead of the staged diff")
    args = parser.parse_args(argv)
    if os.environ.get("SKIP_SENSITIVE_SCAN") == "1":
        print("⚠️  sensitive-content scan skipped (SKIP_SENSITIVE_SCAN=1)", file=sys.stderr)
        return 0

    pfile = patterns_file()
    rules = BUILTIN_CONTENT_RULES + load_patterns(pfile)
    if args.message_file:
        lines = Path(args.message_file).read_text(errors="replace").splitlines()
        findings = [
            f
            for n, line in enumerate(lines, 1)
            if not line.startswith("#")
            for f in scan_text(f"commit message:{n}", line, rules)
        ]
    else:
        diff, paths = staged()
        findings = scan_diff(diff, paths, rules)

    if not findings:
        return 0
    print("⛔ Sensitive-content check failed. This repository is public: remove these before committing.")
    for f in findings:
        print(f)
    local = pfile if pfile and pfile.is_file() else None
    print(f"   Local patterns: {local or 'none (see docs: Operating with agents / automation)'}")
    print("   False positive? Fix the pattern, or bypass once with SKIP_SENSITIVE_SCAN=1 git commit ...")
    return 1


if __name__ == "__main__":
    sys.exit(main())
