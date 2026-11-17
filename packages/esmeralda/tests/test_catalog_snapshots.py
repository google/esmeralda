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

"""Catalog snapshots: schema, normalization, leak guard, fetch, offline resolve and the CLI.

Everything is synthetic: a fake snapshot adapter (``fixture_inventory``) registered for the test,
the fixture snapshots under ``tests/fixtures/catalog_sources/``, no network and no real ids.
Values that look like environment literals (12-digit numbers, emails) are assembled at run time so
that this file itself never contains one.
"""

from __future__ import annotations

import copy
import json
import os
import random
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from esmeralda import catalog, cli
from esmeralda.catalog import (
    CatalogError,
    Placeholders,
    SnapshotSource,
    SourceUnreachable,
    commands,
    register_source,
)
from esmeralda.catalog import sources as catalog_sources
from esmeralda.catalog.snapshot import (
    build_snapshot,
    normalize_entries,
    normalize_placeholders,
    placeholder_problems,
    read_snapshot,
    render_snapshot,
    sha256_text,
    snapshot_leaks,
    substitute_placeholders,
    validate_snapshot,
)
from esmeralda.ops import guard

REPO = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "catalog_sources"
FIXTURE_SNAPSHOT = FIXTURES / "catalog" / "snapshots" / "inventory"
SOURCE_ENTRY = "  - name: inventory\n    type: fixture_inventory\n"

DOMAIN = "example.internal"
REGION = "europe-west9"
PROJECTS = {"pricing": "demo-pricing-proj", "sandbox": "demo-sandbox-proj"}
NUMBER = "1234" + "56789012"  # a synthetic 12-digit project number
ORG = "9876" + "54321"
EMAIL = "someone" + "@" + "example.com"

# What a live system returns: literal ids, volatile fields, arbitrary order.
LIVE = [
    {
        "id": "endpoints/pricing",
        "name": "pricing",
        "etag": "W/1",
        "updateTime": "2026-01-02T03:04:05Z",
        "inventory": {
            "kind": "http",
            "description": "Pricing API (synthetic)",
            "deploy": {"endpoint": f"https://pricing.{DOMAIN}"},
            "runtime": "cloud_run",
            "runtime_resource": (f"//run.googleapis.com/projects/{PROJECTS['pricing']}/locations/{REGION}"
                                 "/services/pricing"),
            "registry": {"id": "endpoints/pricing", "skills": ["quote"], "createTime": "2026-01-01T00:00:00Z"},
        },
    },
    {
        "id": "agents/loan-helper",
        "name": "loan-helper",
        "inventory": {
            "kind": "a2a",
            "description": "Discovered loan helper agent (synthetic)",
            "runtime": "cloud_run",
            "runtime_resource": (f"//run.googleapis.com/projects/{PROJECTS['sandbox']}/locations/{REGION}"
                                 "/services/loan-helper"),
            "registry": {"id": "agents/loan-helper"},
        },
    },
]
PLACEHOLDERS = Placeholders(domain=DOMAIN, region=REGION, projects=PROJECTS)


@pytest.fixture
def live(monkeypatch) -> dict[str, Any]:
    """Register the ``fixture_inventory`` snapshot adapter; mutate the returned state to change the live data."""
    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))
    state: dict[str, Any] = {"entries": copy.deepcopy(LIVE), "unreachable": False, "fetches": 0}

    @register_source("fixture_inventory")
    class FixtureInventory(SnapshotSource):
        CAPABILITIES = {"inventory": ["*"],
                        "targets": ["deploy.endpoint", "runtime", "runtime_resource", "description", "registry"]}

        def placeholders(self) -> Placeholders:
            return PLACEHOLDERS

        def fetch(self) -> list[dict[str, Any]]:
            state["fetches"] += 1
            if state["unreachable"]:
                raise SourceUnreachable(f"{self.name}: the live source can't be reached (synthetic)")
            return copy.deepcopy(state["entries"])

    return state


def _base_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO / "catalog", root / "catalog")
    (root / "contracts").mkdir()
    for name in ("catalog.schema.json", "catalog-snapshot.schema.json"):
        shutil.copy(REPO / "contracts" / name, root / "contracts")
    (root / "infrastructure/live").mkdir(parents=True)
    (root / "infrastructure/live/root.hcl").write_text("")
    return root


def _enable(root: Path, extra: str = "") -> None:
    path = root / "catalog/sources.yaml"
    path.write_text(path.read_text().replace("\n\nenvironments_dir:", f"\n{SOURCE_ENTRY}{extra}\nenvironments_dir:"))


@pytest.fixture
def repo(tmp_path: Path, live) -> Path:
    """A copy of the repository catalog with the fixture snapshot source enabled and one adopted target."""
    root = _base_repo(tmp_path)
    shutil.copytree(FIXTURES / "catalog", root / "catalog", dirs_exist_ok=True)
    _enable(root)
    return root


def _problems(root: Path, env: str = "dev") -> str:
    with pytest.raises(CatalogError) as exc:
        catalog.resolve(root, env)
    return "\n".join(exc.value.problems)


# ------------------------------------------------------------------------------------------
# schema and validation
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["dev", "prd"])
def test_fixture_snapshots_are_valid_and_canonical(env):
    doc, digest = read_snapshot(REPO, FIXTURE_SNAPSHOT / f"{env}.json", f"{env}.json", source="inventory",
                                type_name="fixture_inventory", env=env)
    assert [e["id"] for e in doc["entries"]] == ["agents/loan-helper", "endpoints/pricing"]
    assert digest == sha256_text((FIXTURE_SNAPSHOT / f"{env}.json").read_text())
    assert "fetched_at" not in doc and "fetched_by" in doc


def _doc(**changes: Any) -> dict[str, Any]:
    doc = json.loads((FIXTURE_SNAPSHOT / "dev.json").read_text())
    doc.update(changes)
    return doc


@pytest.mark.parametrize(("change", "expected"), [
    (lambda d: d.pop("entries"), "'entries' is a required property"),
    (lambda d: d.update(fetched_at="2026-01-01T00:00:00Z"), "Additional properties are not allowed"),
    (lambda d: d.update(schema="esmeralda.catalog"), "'esmeralda.catalog.snapshot' was expected"),
    (lambda d: d["entries"].reverse(), "entries must be sorted by id, without duplicates"),
    (lambda d: d["entries"].append(copy.deepcopy(d["entries"][-1])),
     "entries must be sorted by id, without duplicates"),
    (lambda d: d["entries"][0].update(etag="x"), "entries[0].etag: volatile field"),
    (lambda d: d["entries"][0]["inventory"].update(description="{tenant}"), "unknown placeholder(s) {tenant}"),
    (lambda d: d.update(fetched_by="manual"), "fetched_by must be"),
    (lambda d: d["entries"][0]["inventory"].update(description=EMAIL), "email address"),
])
def test_snapshot_validation_rejects(change, expected):
    doc = _doc()
    change(doc)
    problems = "\n".join(validate_snapshot(REPO, doc, "snap.json"))
    assert expected in problems


def test_snapshot_must_match_its_source_environment_and_canonical_form():
    doc = _doc()
    assert "environment is 'dev', expected 'prd'" in "\n".join(validate_snapshot(REPO, doc, "s", env="prd"))
    assert "source is 'inventory', expected 'other'" in "\n".join(validate_snapshot(REPO, doc, "s", source="other"))
    ugly = json.dumps(doc)
    assert "not in canonical form" in "\n".join(validate_snapshot(REPO, doc, "s", text=ugly))


# ------------------------------------------------------------------------------------------
# normalization and placeholders
# ------------------------------------------------------------------------------------------


def test_normalization_drops_volatile_fields_sorts_and_applies_placeholders():
    entries = normalize_entries(LIVE, PLACEHOLDERS)
    assert entries == json.loads((FIXTURE_SNAPSHOT / "dev.json").read_text())["entries"]


def test_placeholders_replace_whole_tokens_longest_first():
    placeholders = Placeholders(org=ORG, projects={"gateway": "demo-gw", "gateway_two": "demo-gw-2"},
                                project_numbers={"gateway": NUMBER}, domain=DOMAIN)
    text = (f"organizations/{ORG}/x projects/demo-gw-2/a projects/demo-gw/b {NUMBER} 9{NUMBER} "
            f"sa@demo-gw.iam.gserviceaccount.com api.{DOMAIN}")
    out = normalize_placeholders({"k": [text]}, placeholders)["k"][0]
    assert out == ("organizations/{org}/x projects/{project:gateway_two}/a projects/{project:gateway}/b "
                   "{project_number:gateway} 9" + NUMBER + " sa@{project:gateway}.iam.gserviceaccount.com api.{domain}")
    assert placeholder_problems(out) == []
    assert substitute_placeholders(out, placeholders) == text


@pytest.mark.parametrize(("placeholders", "expected"), [
    (Placeholders(projects={"a": "same-id", "b": "same-id"}), "literal maps to two placeholders"),
    (Placeholders(project_numbers={"a": "not-digits"}), "must be digits"),
    (Placeholders(projects={"Bad Alias": "x-y"}), "placeholder alias 'Bad Alias'"),
])
def test_placeholder_configuration_errors(placeholders, expected):
    with pytest.raises(CatalogError, match=expected):
        normalize_placeholders("x", placeholders)


def test_colliding_ids_or_names_fail_instead_of_being_ordered():
    with pytest.raises(CatalogError, match="two entries have the id 'a'"):
        normalize_entries([{"id": "a"}, {"id": "a"}])
    with pytest.raises(CatalogError, match="two entries have the name 'x'"):
        normalize_entries([{"id": "a", "name": "x"}, {"id": "b", "name": "x"}])


# ------------------------------------------------------------------------------------------
# leak guard (esmeralda guard)
# ------------------------------------------------------------------------------------------

LEAKS = [
    (f"projects/{NUMBER}/locations/x", "raw 12-digit project number"),
    (f"organizations/{ORG}", "organization or folder id"),
    (EMAIL, "email address"),
    ("user:" + "alice", "IAM member or LDAP-like user"),
    ("eyJ" + "hbGciOiJIUzI1" + "." + "eyJzdWIiOiIxMjM0", "JWT-like token"),
    ("Ab3" + "x" * 30 + "Z9", "token-like string"),
]


@pytest.mark.parametrize(("value", "rule"), LEAKS, ids=[r for _, r in LEAKS])
def test_guard_rejects_environment_literals_and_secrets_in_snapshots(tmp_path, value, rule):
    doc = _doc()
    doc["entries"][0]["inventory"]["description"] = value
    rel = "catalog/snapshots/inventory/dev.json"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(render_snapshot(doc))
    findings = guard.scan_files(tmp_path, [rel])
    assert any(rule in f.rule and f.rule.startswith("catalog snapshot:") for f in findings), findings
    assert all(value not in f.snippet or len(value) <= 8 for f in findings), "findings are masked"
    assert any(rule in r for r, _ in snapshot_leaks(render_snapshot(doc)))

    # the same text outside a snapshot is not a snapshot finding
    (tmp_path / "notes.json").write_text(render_snapshot({"description": value}))
    assert not [f for f in guard.scan_files(tmp_path, ["notes.json"]) if f.rule.startswith("catalog snapshot:")]


def test_guard_recognizes_snapshots_by_header_and_scans_diffs(tmp_path):
    doc = _doc()
    doc["entries"][0]["inventory"]["description"] = EMAIL
    text = render_snapshot(doc)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere/inventory.json").write_text(text)
    assert any(f.rule.startswith("catalog snapshot:") for f in guard.scan_files(tmp_path, ["elsewhere/inventory.json"]))

    added = "".join(f"+{line}\n" for line in text.splitlines())
    rel = "catalog/snapshots/inventory/dev.json"
    diff = (f"diff --git a/{rel} b/{rel}\n--- /dev/null\n+++ b/{rel}\n"
            f"@@ -0,0 +1,{len(text.splitlines())} @@\n{added}")
    findings = guard.scan_diff(diff, [rel])
    assert any("email address" in f.rule for f in findings)


def test_committed_fixture_snapshots_pass_the_guard():
    paths = sorted(p.relative_to(REPO).as_posix() for p in FIXTURES.rglob("*.json"))
    assert paths
    assert guard.scan_files(REPO, paths) == []


# ------------------------------------------------------------------------------------------
# resolve from snapshots
# ------------------------------------------------------------------------------------------


def test_resolve_reads_the_snapshot_and_adopts_by_pin(repo):
    resolved = catalog.resolve(repo, "dev")
    target = resolved.targets["pricing-api"]
    assert target["runtime"] == "cloud_run" and target["description"] == "Pricing API (synthetic)"
    assert resolved.adopted == {"pricing-api": "inventory"}
    assert set(resolved.inventory) == {"loan-helper"}
    origin = "snapshots/inventory/dev.json#endpoints/pricing"
    assert resolved.provenance["targets.pricing-api.runtime"] == {"source": "inventory", "origin": origin,
                                                                  "strategy": "strict"}
    snapshot = repo / "catalog/snapshots/inventory/dev.json"
    assert resolved.document["sources"] == [
        "catalog-files",
        {"name": "inventory", "type": "fixture_inventory", "snapshot": "catalog/snapshots/inventory/dev.json",
         "sha256": sha256_text(snapshot.read_text())},
        "environments/dev.yaml",
    ]
    data = json.loads(catalog.render_resolved_file(resolved))
    assert data["inventory_summary"] == {"inventory": {"adopted": 1, "discovered": 1}}
    assert "inventory" not in data, ".resolved carries only the inventory_summary counts"


def test_files_only_resolved_document_keeps_plain_source_names():
    assert catalog.resolve(REPO, "dev").document["sources"] == ["catalog-files", "environments/dev.yaml"]


def test_shipped_sources_keep_network_sources_disabled():
    rows = {row["name"]: row for row in commands.describe_sources(REPO, "dev")["sources"]}
    assert rows["catalog-files"]["enabled"] is True
    assert rows["agent-registry"]["enabled"] is False and rows["project-labels"]["enabled"] is False
    assert not list((REPO / "catalog").glob("snapshots/**/*.json")), "the demo repo commits no real snapshots"


def test_missing_snapshot_names_the_fetch_command(repo):
    shutil.rmtree(repo / "catalog/snapshots")
    problems = _problems(repo)
    assert "inventory: snapshot catalog/snapshots/inventory/dev.json not found" in problems
    assert "esmeralda catalog fetch --env dev --source inventory" in problems


def test_optional_source_without_snapshot_is_a_warning_until_an_entry_is_adopted(repo):
    shutil.rmtree(repo / "catalog/snapshots")
    path = repo / "catalog/sources.yaml"
    path.write_text(path.read_text().replace(SOURCE_ENTRY, SOURCE_ENTRY + "    options: { optional: true }\n"))
    problems = _problems(repo)
    assert "targets.pricing-api" in problems and "becomes mandatory once a catalog file adopts" in problems

    (repo / "catalog/external/legacy/pricing-api.yaml").unlink()
    resolved = catalog.resolve(repo, "dev")
    assert resolved.inventory == {}
    assert len(resolved.warnings) == 1 and "optional source" in resolved.warnings[0]
    assert resolved.document["sources"][1]["sha256"] is None


def test_snapshot_path_is_configurable_but_stays_inside_the_catalog(repo):
    path = repo / "catalog/sources.yaml"
    (repo / "catalog/snapshots/inventory").rename(repo / "catalog/snapshots/custom")
    text = path.read_text()
    custom = '    options: { snapshot: "snapshots/custom/{env}.json" }\n'
    path.write_text(text.replace(SOURCE_ENTRY, SOURCE_ENTRY + custom))
    assert catalog.resolve(repo, "dev").adopted == {"pricing-api": "inventory"}
    path.write_text(text.replace(SOURCE_ENTRY, SOURCE_ENTRY + custom.replace("snapshots/custom", "../outside")))
    assert "must be a .json path inside catalog/" in _problems(repo)


def test_a_tampered_snapshot_fails_the_resolve(repo):
    path = repo / "catalog/snapshots/inventory/dev.json"
    path.write_text(path.read_text().replace('"kind": "a2a"', '"kind": "a2a", "etag": "x"'))
    assert "volatile field" in _problems(repo) or "not in canonical form" in _problems(repo)


# ------------------------------------------------------------------------------------------
# fetch: idempotent, deterministic, fail closed
# ------------------------------------------------------------------------------------------


def test_fetch_writes_the_normalized_snapshot_and_is_idempotent(repo, live):
    expected = (FIXTURE_SNAPSHOT / "dev.json").read_text()
    path = repo / "catalog/snapshots/inventory/dev.json"
    path.unlink()

    dry = commands.fetch_sources(repo, "dev", "inventory", dry_run=True)
    assert dry["sources"][0]["status"] == "created" and not dry["sources"][0]["written"] and not path.exists()

    first = commands.fetch_sources(repo, "dev", "inventory")
    assert first["sources"][0]["status"] == "created" and path.read_text() == expected
    assert first["sources"][0]["added"] == ["agents/loan-helper", "endpoints/pricing"]
    again = commands.fetch_sources(repo, "dev", "all")
    assert again["sources"][0]["status"] == "unchanged" and path.read_text() == expected


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_fetch_is_independent_of_the_adapter_order(repo, live, seed):
    rng = random.Random(seed)

    def shuffle(value: Any) -> Any:
        if isinstance(value, dict):
            keys = list(value)
            rng.shuffle(keys)
            return {k: shuffle(value[k]) for k in keys}
        return value

    entries = [shuffle(e) for e in LIVE]
    rng.shuffle(entries)
    live["entries"] = entries
    result = commands.fetch_sources(repo, "dev", "inventory")
    assert result["sources"][0]["status"] == "unchanged"


def test_fetch_reports_changes_and_unreachable_sources_write_nothing(repo, live):
    path = repo / "catalog/snapshots/inventory/dev.json"
    before = path.read_text()
    live["entries"] = [e for e in copy.deepcopy(LIVE) if e["id"] != "agents/loan-helper"]
    live["entries"][0]["inventory"]["description"] = "Pricing API v2 (synthetic)"
    dry = commands.fetch_sources(repo, "dev", "inventory", dry_run=True)["sources"][0]
    assert (dry["status"], dry["removed"], dry["changed"]) == ("updated", ["agents/loan-helper"], ["endpoints/pricing"])
    assert path.read_text() == before

    live["unreachable"] = True
    with pytest.raises(SourceUnreachable):
        commands.fetch_sources(repo, "dev", "all")
    assert path.read_text() == before


def test_fetch_refuses_to_write_environment_literals(repo, live):
    path = repo / "catalog/snapshots/inventory/dev.json"
    before = path.read_text()
    live["entries"][1]["inventory"]["description"] = f"owned by {EMAIL}"
    live["entries"][0]["inventory"]["runtime_resource"] = f"//run.googleapis.com/projects/{NUMBER}/services/x"
    with pytest.raises(CatalogError) as exc:
        commands.fetch_sources(repo, "dev", "inventory")
    problems = "\n".join(exc.value.problems)
    assert "email address" in problems and "raw 12-digit project number" in problems and "nothing written" in problems
    assert EMAIL not in problems and NUMBER not in problems, "leak findings are masked"
    assert path.read_text() == before


def test_fetch_source_selection_errors(repo):
    with pytest.raises(CatalogError, match="unknown source 'nope'"):
        commands.fetch_sources(repo, "dev", "nope")
    with pytest.raises(CatalogError, match="is not read from a snapshot"):
        commands.fetch_sources(repo, "dev", "catalog-files")


def test_a_disabled_source_can_be_fetched_before_it_is_enabled(tmp_path, live):
    root = _base_repo(tmp_path)
    _enable(root, "    enabled: false\n")
    assert commands.fetch_sources(root, "dev", "all")["sources"] == []
    result = commands.fetch_sources(root, "dev", "inventory")
    assert result["sources"][0]["status"] == "created"
    assert (root / "catalog/snapshots/inventory/dev.json").read_text() == (FIXTURE_SNAPSHOT / "dev.json").read_text()


HASHSEED_SCRIPT = """
import json, sys
from pathlib import Path
from esmeralda import catalog
from esmeralda.catalog import Placeholders, SnapshotSource, register_source
from esmeralda.catalog import commands

LIVE = json.loads(sys.argv[2])

@register_source("fixture_inventory")
class FixtureInventory(SnapshotSource):
    CAPABILITIES = {"inventory": ["*"],
                    "targets": ["deploy.endpoint", "runtime", "runtime_resource", "description", "registry"]}

    def placeholders(self):
        return Placeholders(**json.loads(sys.argv[3]))

    def fetch(self):
        entries = list(LIVE)
        order = sorted(set(e["id"] for e in entries))  # a set: hash-seed order
        return [next(e for e in entries if e["id"] == i) for i in set(order)]

root = Path(sys.argv[1])
commands.fetch_sources(root, "dev", "inventory")
resolved = catalog.resolve(root, "dev")
snapshot = (root / "catalog/snapshots/inventory/dev.json").read_text()
sys.stdout.write(snapshot + json.dumps(resolved.document) + catalog.render_resolved_file(resolved))
"""


def test_fetch_and_resolve_are_independent_of_the_hash_seed(repo, tmp_path):
    (repo / "catalog/snapshots/inventory/dev.json").unlink()
    script = tmp_path / "fetch_and_resolve.py"
    script.write_text(HASHSEED_SCRIPT)
    placeholders = json.dumps({"domain": DOMAIN, "region": REGION, "projects": PROJECTS})
    outputs = set()
    for seed in ("0", "1", "4242"):
        target = tmp_path / f"seed-{seed}"
        shutil.copytree(repo, target)
        env = {**os.environ, "PYTHONHASHSEED": seed}
        run = subprocess.run([sys.executable, str(script), str(target), json.dumps(LIVE), placeholders], env=env,
                             capture_output=True, text=True, check=False)
        assert run.returncode == 0, run.stderr
        outputs.add(run.stdout)
    assert len(outputs) == 1
    assert next(iter(outputs)).startswith((FIXTURE_SNAPSHOT / "dev.json").read_text())


# ------------------------------------------------------------------------------------------
# offline invariant (I6)
# ------------------------------------------------------------------------------------------


def test_resolve_check_show_and_diff_open_no_socket(repo, monkeypatch, capsys):
    def no_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the catalog opened a network socket")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    args = ["--repo", str(repo)]
    assert cli.main(["catalog", "resolve", "--env", "all", "--write", *args]) == 0
    assert cli.main(["catalog", "resolve", "--env", "all", "--check", *args]) == 0
    assert cli.main(["catalog", "resolve", "--env", "dev", *args]) == 0
    assert cli.main(["catalog", "show", "--env", "dev", "--provenance", *args]) == 0
    assert cli.main(["catalog", "show", "--env", "dev", "--inventory", "--json", *args]) == 0
    assert cli.main(["catalog", "sources", "--env", "dev", *args]) == 0
    assert cli.main(["catalog", "diff", "--env", "dev", *args]) == 3
    assert "the catalog opened a network socket" not in capsys.readouterr().err


# ------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------


def _json(capsys) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


def test_cli_sources_lists_types_capabilities_and_snapshots(repo, capsys):
    assert cli.main(["catalog", "sources", "--env", "dev", "--json", "--repo", str(repo)]) == 0
    rows = {row["name"]: row for row in _json(capsys)["sources"]}
    inv = rows["inventory"]
    assert inv["snapshot"] == "catalog/snapshots/inventory/dev.json" and inv["snapshot_status"] == "ok"
    assert inv["entries"] == 2 and len(inv["sha256"]) == 64
    assert inv["capabilities"]["inventory"] == ["*"]
    assert rows["catalog-files"]["capabilities"].startswith("everything")
    assert rows["agent-registry"]["enabled"] is False
    assert cli.main(["catalog", "sources", "--env", "dev", "--repo", str(repo)]) == 0
    assert "inventory (fixture_inventory, enabled)" in capsys.readouterr().out


def test_cli_fetch_exit_codes(repo, live, capsys):
    args = ["--env", "dev", "--repo", str(repo)]
    assert cli.main(["catalog", "fetch", "--source", "inventory", "--dry-run", *args]) == 0
    assert "unchanged" in capsys.readouterr().out
    assert cli.main(["catalog", "fetch", "--source", "nope", *args]) == 1
    assert cli.main(["catalog", "fetch", "--source", "catalog-files", *args]) == 1
    live["unreachable"] = True
    assert cli.main(["catalog", "fetch", "--source", "all", *args]) == 2
    assert "can't be reached" in capsys.readouterr().err
    assert cli.main(["catalog", "fetch", "--source", "all", "--env", "all", "--repo", str(repo)]) == 1


def test_cli_diff_offline_and_live(repo, live, capsys):
    args = ["--env", "dev", "--json", "--repo", str(repo)]
    assert cli.main(["catalog", "diff", *args]) == 3
    findings = _json(capsys)["findings"]
    assert [(f["kind"], f["subject"]) for f in findings] == [("discovered", "inventory.loan-helper")]

    assert cli.main(["catalog", "diff", "--live", *args]) == 3
    assert [f["kind"] for f in _json(capsys)["findings"]] == ["discovered"], "live equals the snapshot"

    live["entries"] = [e for e in copy.deepcopy(LIVE) if e["id"] != "agents/loan-helper"]
    assert cli.main(["catalog", "diff", "--live", *args]) == 3
    drift = [f for f in _json(capsys)["findings"] if f["kind"] == "live_drift"]
    assert [f["subject"] for f in drift] == ["catalog/snapshots/inventory/dev.json#agents/loan-helper"]
    assert "removed" in drift[0]["detail"]
    live["unreachable"] = True
    assert cli.main(["catalog", "diff", "--live", *args]) == 2


def test_cli_diff_shadowed_values_and_clean_catalog(repo, capsys):
    path = repo / "catalog/external/legacy/pricing-api.yaml"
    path.write_text(path.read_text() + "description: Pricing API from the catalog file\n")
    assert cli.main(["catalog", "diff", "--env", "dev", "--json", "--repo", str(repo)]) == 3
    kinds = {(f["kind"], f["subject"]) for f in _json(capsys)["findings"]}
    assert ("shadowed", "targets.pricing-api.description") in kinds

    (repo / "catalog/snapshots/inventory/dev.json").unlink()
    shutil.copy(REPO / "catalog/sources.yaml", repo / "catalog/sources.yaml")
    path.unlink()
    assert cli.main(["catalog", "diff", "--env", "dev", "--repo", str(repo)]) == 0
    assert "No catalog drift" in capsys.readouterr().out


def test_cli_show_provenance_and_inventory(repo, capsys):
    args = ["--env", "dev", "--repo", str(repo)]
    assert cli.main(["catalog", "show", "--target", "pricing-api", "--provenance", "--json", *args]) == 0
    row = _json(capsys)["targets"][0]
    assert row["adopted_from"] == "inventory"
    assert row["provenance"]["entry"]["origin"] == "catalog/external/legacy/pricing-api.yaml"
    assert row["provenance"]["fields"]["runtime"]["source"] == "inventory"
    assert cli.main(["catalog", "show", "--target", "pricing-api", "--provenance", *args]) == 0
    out = capsys.readouterr().out
    assert "adopted from inventory" in out
    assert "runtime: inventory (snapshots/inventory/dev.json#endpoints/pricing" in out

    assert cli.main(["catalog", "show", "--inventory", "--provenance", "--json", *args]) == 0
    rows = _json(capsys)["inventory"]
    assert [(r["name"], r["id"], r["compiled"]) for r in rows] == [("loan-helper", "agents/loan-helper", False)]
    assert rows[0]["provenance"]["entry"]["source"] == "inventory"
    assert cli.main(["catalog", "show", "--inventory", *args]) == 0
    assert "loan-helper (a2a) id=agents/loan-helper from inventory" in capsys.readouterr().out


def test_cli_show_on_the_repository_catalog_is_unchanged_without_the_new_flags(capsys):
    assert cli.main(["catalog", "show", "--env", "dev", "--json", "--repo", str(REPO)]) == 0
    data = _json(capsys)
    assert all("provenance" not in row and "adopted_from" not in row for row in data["targets"])
    assert cli.main(["catalog", "show", "--env", "dev", "--inventory", "--repo", str(REPO)]) == 0
    assert "(none)" in capsys.readouterr().out


def test_build_snapshot_round_trips_through_the_validator():
    doc = build_snapshot(source="s", type_name="t", env="dev", entries=[{"id": "b"}, {"id": "a"}])
    assert [e["id"] for e in doc["entries"]] == ["a", "b"]
    assert validate_snapshot(REPO, doc, "s", text=render_snapshot(doc)) == []
