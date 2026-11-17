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

"""Docs never hard-code private IPv4 addresses.

Addresses such as the entry point VIP are picked when an environment is applied, so a literal in
the docs is wrong for every other deployment and goes stale. Docs name the Terraform output or
the ``esmeralda status --env <env> --unit <unit> --output <name>`` command that reports the value
instead (or a placeholder such as ``<entrypoint-ip>``).

The only literals allowed are the blueprint's design values that Terraform sets itself (the
subnet CIDR ranges and the fixed Secure Web Proxy address). Each allowlist entry names the source
file that configures it and the docs pages that may cite it; the guard fails when the value
disappears from its source (so the docs would be wrong) or when no page cites it any more.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

NETWORKING_MAIN = "infrastructure/modules/2-networking/main.tf"
PRIVATE_NETWORKING_DOC = "docs/src/1-platform-foundations/02-private-networking.md"

# Private IPv4 literals, with an optional /prefix: RFC 1918 (10/8, 172.16/12, 192.168/16) and
# the RFC 6598 shared address space (100.64/10). Not part of a longer dotted number.
PRIVATE_IPV4 = re.compile(
    r"(?<![\d.])"
    r"(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})"
    r"(?:/\d{1,2})?"
    r"(?![\d.]*\d)"
)

# literal -> (file that configures it, docs pages allowed to cite it, reason)
ALLOWED_LITERALS: dict[str, tuple[str, frozenset[str], str]] = {
    "10.0.1.0/24": (
        NETWORKING_MAIN,
        frozenset({PRIVATE_NETWORKING_DOC}),
        "core subnet CIDR (ip_cidr_range of sb-esmeralda-core-<env>), same in every env",
    ),
    "10.9.0.0/24": (
        NETWORKING_MAIN,
        frozenset({PRIVATE_NETWORKING_DOC}),
        "proxy-only subnet CIDR (ip_cidr_range of sb-esmeralda-proxy-<env>), same in every env",
    ),
    "10.10.0.0/24": (
        NETWORKING_MAIN,
        frozenset({PRIVATE_NETWORKING_DOC}),
        "PSC endpoint subnet CIDR (ip_cidr_range of sb-esmeralda-psc-<env>), same in every env",
    ),
    "10.11.0.0/24": (
        NETWORKING_MAIN,
        frozenset({PRIVATE_NETWORKING_DOC}),
        "PSC interface subnet CIDR (ip_cidr_range of sb-esmeralda-psc-interface-<env> and the "
        "allow-psc-interface-ingress firewall source range), same in every env",
    ),
    "10.0.1.100": (
        NETWORKING_MAIN,
        frozenset({PRIVATE_NETWORKING_DOC}),
        "fixed Secure Web Proxy address (gateway_config.addresses, output secure_web_proxy_ip)",
    ),
}

DOC_SUFFIXES = {".md", ".txt", ".yml", ".yaml", ".html"}


def _doc_files() -> list[str]:
    """Tracked docs: every Markdown file (README, AGENTS.md, app READMEs) and the docs/ site."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    files = []
    for rel in out.splitlines():
        suffix = pathlib.PurePosixPath(rel).suffix.lower()
        if suffix == ".md" or (rel.startswith("docs/") and suffix in DOC_SUFFIXES):
            if (REPO_ROOT / rel).is_file():
                files.append(rel)
    return sorted(files)


def _literals(rel: str) -> list[tuple[int, str]]:
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    return [
        (lineno, match.group(0))
        for lineno, line in enumerate(text.splitlines(), 1)
        for match in PRIVATE_IPV4.finditer(line)
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("VIP 10.0.1.2 here", ["10.0.1.2"]),
        ("(`partner.example.internal` / `10.0.1.3`)", ["10.0.1.3"]),
        ("range 172.16.0.0/12 and 172.31.255.1", ["172.16.0.0/12", "172.31.255.1"]),
        ("home 192.168.1.10:8080", ["192.168.1.10"]),
        ("cgnat 100.64.0.1", ["100.64.0.1"]),
        ("public 8.8.8.8, 35.235.240.0/20, 172.32.0.1, 100.128.0.1", []),
        ("versions 1.10.0.0.1 and v210.0.1.2", []),
        ("<entrypoint-ip>", []),
    ],
)
def test_private_ipv4_pattern(text, expected):
    assert [m.group(0) for m in PRIVATE_IPV4.finditer(text)] == expected


def test_docs_have_no_hardcoded_private_ips():
    offenders = []
    for rel in _doc_files():
        for lineno, literal in _literals(rel):
            allowed = ALLOWED_LITERALS.get(literal)
            if allowed is None or rel not in allowed[1]:
                offenders.append(f"{rel}:{lineno}: {literal}")
    assert not offenders, (
        "Hard-coded private IPs in the docs. Replace each with a placeholder (e.g. <entrypoint-ip>) "
        "and name the output that reports it (e.g. `uv run esmeralda status --env <env> --unit "
        "layer-2-networking --output entrypoint_address`), or, for a design value that Terraform "
        "sets, add it to ALLOWED_LITERALS with its source and reason:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("literal", sorted(ALLOWED_LITERALS))
def test_allowed_literal_is_configured_by_its_source(literal):
    source, _pages, reason = ALLOWED_LITERALS[literal]
    assert reason.strip()
    assert f'"{literal}"' in (REPO_ROOT / source).read_text(encoding="utf-8"), (
        f"{literal} is no longer set in {source}: update the docs that cite it and the allowlist"
    )


@pytest.mark.parametrize("literal", sorted(ALLOWED_LITERALS))
def test_allowed_literal_is_still_cited(literal):
    _source, pages, _reason = ALLOWED_LITERALS[literal]
    cited = [rel for rel in sorted(pages) if any(lit == literal for _, lit in _literals(rel))]
    assert cited, f"{literal} is no longer cited by {sorted(pages)}: drop it from ALLOWED_LITERALS"
