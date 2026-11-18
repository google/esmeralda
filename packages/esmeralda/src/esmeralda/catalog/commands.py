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

"""``esmeralda catalog sources | fetch | diff``: list the sources, refresh snapshots, report drift.

* :func:`describe_sources` (offline): every source of ``sources.yaml``, enabled or not, with its
  type, capabilities and, for snapshot sources, the snapshot path, sha256 and entry count.
* :func:`fetch_sources` (network, read-only on the sources): calls each adapter's ``fetch()``,
  normalizes the result and writes ``catalog/snapshots/<source>/<env>.json``. Every source is
  fetched before anything is written, so an unreachable source (:class:`SourceUnreachable`, exit 2)
  leaves every snapshot unchanged. Fetching the same live state twice gives the same bytes.
* :func:`diff_catalog` (offline unless ``live``): findings a reviewer should look at: discovered
  entries no catalog file adopts, external values shadowed by catalog files or overridden by the
  environment, source data that disagrees with the catalog files (``label_mismatch``: project
  labels vs declared teams, cost centres and owners; listed whatever the policy), optional sources
  without a snapshot and, with ``live``, live entries that differ from the committed snapshot
  and what the live system is missing compared with the compiled catalog (``missing``: for Agent
  Registry, entries Esmeralda registers that the registry doesn't list); never written.
* :func:`drift_report` (the ``catalog-drift`` step of ``esmeralda verify``): the same findings,
  counted per kind, where an unreachable live source is a notice instead of an error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from esmeralda.catalog.merge import source_capabilities
from esmeralda.catalog.resolver import _sources_doc, catalog_dir, inventory_rows, resolve
from esmeralda.catalog.schema import CatalogError, load_schema
from esmeralda.catalog.snapshot import SnapshotSource, SourceUnreachable, fetch_command, render_snapshot, sha256_text
from esmeralda.catalog.sources import FilesSource, Source, build_source, source_name

__all__ = ["CatalogError", "SourceUnreachable", "describe_sources", "diff_catalog", "drift_report", "fetch_sources"]


def _all_sources(root: Path, env: str) -> list[tuple[dict[str, Any], Source | CatalogError]]:
    """Every sources.yaml entry with its built source (or the error building it)."""
    schema = load_schema(root)
    doc = _sources_doc(root, schema)
    out: list[tuple[dict[str, Any], Source | CatalogError]] = []
    for i, spec in enumerate(doc["sources"]):
        try:
            out.append((spec, build_source(root, catalog_dir(root), schema, spec, i, env)))
        except CatalogError as exc:
            out.append((spec, exc))
    return out


def _capabilities(source: Source) -> str | dict[str, list[str]]:
    caps = source_capabilities(source)
    if caps is None:
        return "everything (catalog files hold the authorization data)"
    return {section: sorted(paths) for section, paths in sorted(caps.items()) if paths}


def describe_sources(root: Path, env: str) -> dict[str, Any]:
    """The sources of ``catalog/sources.yaml`` in merge order (offline)."""
    rows = []
    for i, (spec, source) in enumerate(_all_sources(root, env)):
        row: dict[str, Any] = {
            "name": source_name(spec, i),
            "type": spec["type"],
            "enabled": spec.get("enabled", True) is not False,
        }
        if isinstance(source, CatalogError):
            row["error"] = "; ".join(source.problems)
            rows.append(row)
            continue
        try:
            row["capabilities"] = _capabilities(source)
        except CatalogError as exc:
            row["error"] = "; ".join(exc.problems)
        if isinstance(source, FilesSource):
            row["paths"] = list(spec.get("paths") or ["."])
        elif isinstance(source, SnapshotSource):
            row["optional"] = source.optional
            try:
                row["snapshot"] = source.snapshot_label()
                found = source.read()
            except CatalogError as exc:
                row["snapshot_status"] = "invalid"
                row["problems"] = exc.problems
            else:
                if found is None:
                    row["snapshot_status"] = "missing"
                    row["fetch"] = fetch_command(env, source.name)
                else:
                    doc, digest = found
                    row.update(snapshot_status="ok", sha256=digest, entries=len(doc["entries"]))
        else:
            row["snapshot"] = None
        rows.append(row)
    return {"env": env, "sources": rows}


def _snapshot_sources(root: Path, env: str, which: str) -> list[SnapshotSource]:
    """``all``: every enabled snapshot source; a name: that source (enabled or not, so a new
    source can be fetched before it is enabled)."""
    built = _all_sources(root, env)
    names = [source_name(spec, i) for i, (spec, _) in enumerate(built)]
    picked: list[SnapshotSource] = []
    for i, (spec, source) in enumerate(built):
        name = names[i]
        if which != "all" and name != which:
            continue
        if isinstance(source, CatalogError):
            raise source
        if not isinstance(source, SnapshotSource):
            if which != "all":
                raise CatalogError(f"source '{name}' (type {spec['type']}) is not read from a snapshot, so there is "
                                   "nothing to fetch")
            continue
        if which == "all" and spec.get("enabled", True) is False:
            continue
        picked.append(source)
    if which != "all" and not picked:
        raise CatalogError(f"unknown source '{which}' (sources.yaml lists: {', '.join(names)})")
    return picked


def _entry_changes(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, list[str]]:
    before = {e["id"]: e for e in old}
    after = {e["id"]: e for e in new}
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(i for i in set(after) & set(before) if after[i] != before[i]),
    }


def _old_entries(text: str | None) -> list[dict[str, Any]]:
    if text is None:
        return []
    try:
        entries = json.loads(text).get("entries") or []
        return [e for e in entries if isinstance(e, dict) and isinstance(e.get("id"), str)]
    except (ValueError, AttributeError):
        return []


def fetch_sources(root: Path, env: str, source: str = "all", *, dry_run: bool = False) -> dict[str, Any]:
    """Fetch live data into snapshots (or, with ``dry_run``, report what would change).

    Raises :class:`SourceUnreachable` (nothing written) when a live system can't be read.
    """
    picked = _snapshot_sources(root, env, source)
    fetched = [(s, render_snapshot(s.fetch_snapshot())) for s in picked]  # every fetch before any write
    results = []
    for src, text in fetched:
        path = src.snapshot_path()
        old_text = path.read_text() if path.is_file() else None
        status = "unchanged" if old_text == text else ("created" if old_text is None else "updated")
        if status != "unchanged" and not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        results.append({
            "name": src.name,
            "type": src.type_name,
            "snapshot": src.snapshot_label(),
            "status": status,
            "written": status != "unchanged" and not dry_run,
            "sha256": sha256_text(text),
            "entries": len(json.loads(text)["entries"]),
            **_entry_changes(_old_entries(old_text), json.loads(text)["entries"]),
        })
    return {"env": env, "dry_run": dry_run, "sources": results}


def _finding(kind: str, source: str | None, subject: str, detail: str) -> dict[str, Any]:
    return {"kind": kind, "source": source, "subject": subject, "detail": detail}


def _offline_findings(resolved: Any) -> list[dict[str, Any]]:
    """Findings that need no network: source findings, missing optional snapshots, discovered
    entries, and external values shadowed or overridden."""
    findings: list[dict[str, Any]] = [dict(f) for f in resolved.findings]
    for warning in resolved.warnings:
        findings.append(_finding("missing_snapshot", warning.split(":", 1)[0], "snapshot", warning))
    for row in inventory_rows(resolved):
        findings.append(_finding(
            "discovered", row["source"], f"inventory.{row['name']}",
            f"{row['kind']} '{row['id'] or row['name']}' ({row['origin']}) is not adopted by any catalog file: never "
            "compiled; to enforce it, add a catalog file with deploy.registry: {source, id} and its callers"))
    for key in sorted(resolved.provenance):
        record = resolved.provenance[key]
        for loser in record.get("shadowed") or []:
            kind = "overridden" if record.get("strategy") == "override" else "shadowed"
            findings.append(_finding(
                kind, loser["source"], key,
                f"{loser['source']} ({loser['origin']}) has another value (sha256 {loser['value_hash']}); "
                f"{record['source']} ({record['origin']}) wins"))
    return findings


def _live_findings(src: SnapshotSource, env: str) -> list[dict[str, Any]]:
    """Live entries that differ from the committed snapshot, plus the source's own live findings
    (network; raises :class:`SourceUnreachable` when the live system can't be read)."""
    findings = []
    live_doc = src.fetch_snapshot()
    found = src.read()
    snapshot_entries = found[0]["entries"] if found else []
    changes = _entry_changes(snapshot_entries, live_doc["entries"])
    for change, ids in changes.items():
        for entry_id in ids:
            findings.append(_finding(
                "live_drift", src.name, f"{src.snapshot_label()}#{entry_id}",
                f"{change} in the live source since the snapshot: run `{fetch_command(env, src.name)}` "
                "and review the change"))
    for f in src.live_findings():
        findings.append(_finding(f["kind"], src.name, f["subject"], f["detail"]))
    return findings


def _sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(findings, key=lambda f: (f["kind"], f["source"] or "", f["subject"], f["detail"]))


def diff_catalog(root: Path, env: str, *, live: bool = False) -> dict[str, Any]:
    """Drift findings for ``env`` (empty list: nothing to review). Offline unless ``live``."""
    resolved = resolve(root, env, strict_findings=False)
    findings = _offline_findings(resolved)
    if live:
        for src in _snapshot_sources(root, env, "all"):
            findings += _live_findings(src, env)
    return {"env": env, "live": live, "findings": _sort_findings(findings)}


DRIFT_MODES = ("warn", "fail")


def drift_mode(policy: dict[str, Any]) -> str:
    """``policy.verify.catalog_drift``: ``warn`` (default) or ``fail``."""
    return (policy.get("verify") or {}).get("catalog_drift", "warn")


def _external_sources(root: Path, env: str) -> tuple[list[str], list[str]]:
    """Names of the enabled non-``files`` sources, and the problems building any of them."""
    names, problems = [], []
    for i, (spec, source) in enumerate(_all_sources(root, env)):
        if spec.get("enabled", True) is False or spec["type"] == "files":
            continue
        names.append(source_name(spec, i))
        if isinstance(source, CatalogError):
            problems += source.problems
    return names, problems


def drift_report(root: Path, env: str, *, live: bool = True) -> dict[str, Any]:
    """The ``catalog-drift`` step of ``esmeralda verify``: every offline finding of
    :func:`diff_catalog` and, with ``live``, the live findings of each enabled snapshot source.

    It never raises for drift: an unreachable source becomes a ``notice`` (the other sources are
    still checked), and a catalog that doesn't resolve becomes an ``error``. ``mode`` is
    ``policy.verify.catalog_drift`` (``warn`` unless the catalog says ``fail``); the caller decides
    what a finding means. With no enabled external source it returns at once, with no findings.
    """
    report: dict[str, Any] = {
        "env": env, "live": live, "sources": [], "mode": "warn", "findings": [], "counts": {}, "notices": [],
        "command": f"esmeralda catalog diff --env {env}" + (" --live" if live else ""),
    }
    try:
        names, problems = _external_sources(root, env)
    except CatalogError as exc:
        report["error"] = "; ".join(exc.problems)
        return report
    report["sources"] = names
    if problems:
        report["error"] = "; ".join(problems)
        return report
    if not names:
        return report
    try:
        resolved = resolve(root, env, strict_findings=False)
    except CatalogError as exc:
        report["error"] = "; ".join(exc.problems)
        return report
    mode = drift_mode(resolved.policy)
    report["mode"] = mode if mode in DRIFT_MODES else "warn"
    findings = _offline_findings(resolved)
    if live:
        report["live_checked"] = []
        try:
            picked = _snapshot_sources(root, env, "all")
        except CatalogError as exc:
            picked = []
            report["notices"].append(f"live check skipped: {'; '.join(exc.problems)}")
        for src in picked:
            try:
                findings += _live_findings(src, env)
            except CatalogError as exc:  # SourceUnreachable included: report it, check the rest
                what = "unreachable" if isinstance(exc, SourceUnreachable) else "live check failed"
                report["notices"].append(f"{src.name}: {what}: {'; '.join(exc.problems)}")
            except Exception as exc:  # noqa: BLE001 - a broken adapter must not break verify
                report["notices"].append(f"{src.name}: live check failed: {type(exc).__name__}: {exc}")
            else:
                report["live_checked"].append(src.name)
    report["findings"] = _sort_findings(findings)
    counts: dict[str, int] = {}
    for f in report["findings"]:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    report["counts"] = dict(sorted(counts.items()))
    return report
