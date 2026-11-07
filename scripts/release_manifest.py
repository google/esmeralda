#!/usr/bin/env python3
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
"""Writes and reads the release manifest (release/<env>.yaml).

The manifest is the single source of truth for which image bytes an environment runs: one
sha256 digest per image, plus where the release came from and when it was verified. Terragrunt
reads it with yamldecode() (infrastructure/live/prd/env.yaml); scripts/promote_release.sh writes
it; scripts/verify_images.sh compares running workloads against it.

Standard library only (runs with any python3, outside the uv workspace).

  release_manifest.py write --out release/prd.yaml --version v1.1.0 --source-tag dev-abc1234 \\
      --source-commit <sha> --verified-env dev --verified-date 2026-01-31 --verified-result PASS \\
      --image envoy-gateway=sha256:... [--image ...]
  release_manifest.py get --manifest release/prd.yaml --key version
  release_manifest.py get --manifest release/prd.yaml --image envoy-gateway
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1

VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_RE = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)*$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ENV_RE = re.compile(r"^[a-z][a-z0-9-]*$")
RESULTS = ("PASS",)

HEADER = """\
# Release manifest: the exact image bytes this environment deploys.
# Written by `make promote VERSION=vX.Y.Z SOURCE=dev-<sha>` (scripts/promote_release.sh);
# do not edit by hand. Merge it by PR, then deploy; roll back by reverting that PR.
#
# Schema (version 1):
#   schema_version: 1
#   version:        release version, also the immutable tag in the release repository (vX.Y.Z)
#   source_tag:     dev repository tag the digests were resolved from (dev-<gitsha>)
#   source_commit:  git commit the images were built from
#   promoted_at:    UTC timestamp of the promotion (RFC 3339)
#   repository:     layer-0 repository the digests live in (release); the registry URL itself
#                   comes from the layer-0 Terragrunt outputs, so no project id is stored here
#   verified:       the dev verification this release passed before promotion
#     environment:  environment that was verified (dev)
#     date:         YYYY-MM-DD
#     result:       PASS (`make verify ENV=dev`: gateway probes + both agents answer)
#   images:         image name => sha256 digest; every Layer 5 workload image must be listed.
#                   Deployed as <release repository URL>/<image>@<digest>.
"""


class ManifestError(ValueError):
    """Invalid manifest input."""


def _check(regex: re.Pattern[str], value: str, what: str) -> str:
    if not isinstance(value, str) or not regex.match(value):
        raise ManifestError(f"invalid {what}: {value!r}")
    return value


def parse_images(pairs: list[str]) -> dict[str, str]:
    """Parses ["name=sha256:...", ...] into an ordered, validated mapping."""
    images: dict[str, str] = {}
    for pair in pairs:
        name, sep, digest = pair.partition("=")
        if not sep:
            raise ManifestError(f"--image must be <name>=<sha256:digest>, got {pair!r}")
        _check(IMAGE_RE, name, "image name")
        _check(DIGEST_RE, digest, f"digest for {name}")
        if name in images and images[name] != digest:
            raise ManifestError(f"image {name} listed twice with different digests")
        images[name] = digest
    if not images:
        raise ManifestError("at least one --image is required")
    return images


def render(
    *,
    version: str,
    source_tag: str,
    source_commit: str,
    promoted_at: dt.datetime,
    verified_env: str,
    verified_date: str,
    verified_result: str,
    images: dict[str, str],
    repository: str = "release",
) -> str:
    """Returns the manifest text (header + YAML body). Validates every field."""
    _check(VERSION_RE, version, "version (expected vX.Y.Z)")
    _check(TAG_RE, source_tag, "source tag")
    _check(COMMIT_RE, source_commit, "source commit")
    _check(ENV_RE, verified_env, "verified environment")
    _check(DATE_RE, verified_date, "verified date (expected YYYY-MM-DD)")
    try:
        dt.date.fromisoformat(verified_date)
    except ValueError as exc:
        raise ManifestError(f"invalid verified date {verified_date!r}: {exc}") from exc
    if verified_result not in RESULTS:
        raise ManifestError(f"verified result must be one of {RESULTS}, got {verified_result!r}")
    _check(ENV_RE, repository, "repository")
    if not images:
        raise ManifestError("images must not be empty")
    for name, digest in images.items():
        _check(IMAGE_RE, name, "image name")
        _check(DIGEST_RE, digest, f"digest for {name}")
    if promoted_at.tzinfo is None:
        raise ManifestError("promoted_at must be timezone-aware")
    stamp = promoted_at.astimezone(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = [
        f"schema_version: {SCHEMA_VERSION}",
        f'version: "{version}"',
        f'source_tag: "{source_tag}"',
        f'source_commit: "{source_commit}"',
        f'promoted_at: "{stamp}"',
        f'repository: "{repository}"',
        "verified:",
        f'  environment: "{verified_env}"',
        f'  date: "{verified_date}"',
        f'  result: "{verified_result}"',
        "images:",
    ]
    lines += [f'  {name}: "{images[name]}"' for name in sorted(images)]
    return HEADER + "\n" + "\n".join(lines) + "\n"


def parse(text: str) -> dict:
    """Reads a manifest written by render() (flat keys, one level of nested maps)."""
    data: dict = {}
    section: dict | None = None
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue
        indented = line.startswith("  ")
        key, sep, value = line.strip().partition(":")
        if not sep:
            raise ManifestError(f"unparseable line: {raw!r}")
        value = value.strip().strip('"')
        if indented:
            if section is None:
                raise ManifestError(f"unexpected indentation: {raw!r}")
            section[key] = value
        elif value == "":
            section = data.setdefault(key, {})
        else:
            section = None
            data[key] = value
    return data


def _cmd_write(args: argparse.Namespace) -> int:
    text = render(
        version=args.version,
        source_tag=args.source_tag,
        source_commit=args.source_commit,
        promoted_at=dt.datetime.now(dt.timezone.utc),
        verified_env=args.verified_env,
        verified_date=args.verified_date,
        verified_result=args.verified_result,
        images=parse_images(args.image),
        repository=args.repository,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(out)
    print(f"wrote {out}")
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    path = Path(args.manifest)
    if not path.is_file():
        return 2
    data = parse(path.read_text())
    if args.image:
        value = data.get("images", {}).get(args.image, "")
    else:
        value = data
        for part in args.key.split("."):
            value = value.get(part, "") if isinstance(value, dict) else ""
    if not value or isinstance(value, dict):
        return 1
    print(value)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("write", help="write a manifest")
    w.add_argument("--out", required=True)
    w.add_argument("--version", required=True)
    w.add_argument("--source-tag", required=True)
    w.add_argument("--source-commit", required=True)
    w.add_argument("--repository", default="release")
    w.add_argument("--verified-env", default="dev")
    w.add_argument("--verified-date", required=True)
    w.add_argument("--verified-result", default="PASS")
    w.add_argument("--image", action="append", default=[], help="<name>=sha256:<digest> (repeat)")
    w.set_defaults(func=_cmd_write)

    g = sub.add_parser("get", help="print one value (exit 1 if absent, 2 if no manifest)")
    g.add_argument("--manifest", required=True)
    group = g.add_mutually_exclusive_group(required=True)
    group.add_argument("--key", help="top-level or dotted key, e.g. version, verified.date")
    group.add_argument("--image", help="image name: prints its digest")
    g.set_defaults(func=_cmd_get)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ManifestError as exc:
        print(f"release_manifest: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
