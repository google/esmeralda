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

"""The ``project_labels`` catalog source: fetch, snapshot, resolve-time attribution and findings.

Everything is synthetic: recorded ``gcloud projects describe`` responses and layer-1 outputs are
served by fakes (``esmeralda.catalog.adapters._live``), so no test touches the network. Values that
look like environment literals (12-digit numbers, an organization id) are assembled at run time so
that this file never contains one.
"""

from __future__ import annotations

import copy
import json
import random
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest

from esmeralda import catalog, cli
from esmeralda.catalog import CatalogError, Layer, SourceUnreachable, commands, register_source
from esmeralda.catalog import sources as catalog_sources
from esmeralda.catalog.adapters import _live
from esmeralda.catalog.adapters.project_labels import ProjectLabelsSource, project_alias
from esmeralda.catalog.snapshot import snapshot_leaks
from esmeralda.ops import guard

REPO = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "catalog_sources"
GOLDEN = FIXTURES / "catalog" / "snapshots" / "project-labels" / "dev.json"
SNAPSHOT = "catalog/snapshots/project-labels/dev.json"
COMPILED = ("catalog", "external_targets", "google_apis", "iap_egress")

ORG = "9876" + "54321"
IDS = {
    "gateway": "demo-gateway-a1b2",
    "mcps": "demo-mcps-a1b2",
    "ai_coe_agents": "demo-ai-coe-agents-a1b2",
    "cx_agents": "demo-cx-agents-a1b2",
}
NUMBERS = {alias: str(100000000000 + i * 1111) for i, alias in enumerate(sorted(IDS), start=1)}
LABELS = {
    "gateway": {"team": "platform", "cost-center": "cc-platform"},
    "mcps": {"team": "appdev-tools", "cost-center": "central-developer-tools"},
    "ai_coe_agents": {"team": "ai-coe", "cost-center": "cc-ai-coe", "agent_platform": "agent-spoke-project"},
    "cx_agents": {"team": "cx", "cost-center": "cc-cx-br"},
}
OPTIONS = """\
    options:
      team_label: team
      cost_center_label: cost-center
      team_map: { appdev-tools: platform }
      cost_center_map: { central-developer-tools: cc-platform }
"""


def _describe(alias: str) -> dict[str, Any]:
    """A recorded ``gcloud projects describe`` response (synthetic)."""
    return {
        "createTime": "2026-01-01T00:00:00.000Z",
        "labels": {**LABELS[alias], "env": "dev", "managed-by": "terragrunt-esmeralda"},
        "lifecycleState": "ACTIVE",
        "name": IDS[alias],
        "parent": {"id": ORG, "type": "organization"},
        "projectId": IDS[alias],
        "projectNumber": NUMBERS[alias],
    }


@pytest.fixture
def live(monkeypatch) -> dict[str, Any]:
    """Fake layer-1 outputs and gcloud; mutate the returned state to change what is live."""
    state: dict[str, Any] = {
        "outputs": {f"{alias}_project_id": pid for alias, pid in IDS.items()} | {"governance_project_id": ""},
        "describe": {IDS[alias]: _describe(alias) for alias in IDS},
        "calls": [],
        "unreachable": False,
    }

    def unit_outputs(_root: Path, env: str, unit: str) -> dict[str, Any]:
        assert env == "dev"
        return copy.deepcopy(state["outputs"]) if unit == "layer-1-projects" else {}

    def gcloud_json(_root: Path, args: list[str], what: str) -> Any:
        state["calls"].append(args)
        if state["unreachable"]:
            raise SourceUnreachable(f"{what} failed: synthetic outage")
        assert args[:2] == ["projects", "describe"], "read-only calls only"
        return copy.deepcopy(state["describe"][args[2]])

    monkeypatch.setattr(_live, "unit_outputs", unit_outputs)
    monkeypatch.setattr(_live, "gcloud_json", gcloud_json)
    return state


def _set_source(root: Path, body: str = "", enabled: bool = True) -> None:
    """Replace the project-labels entry of sources.yaml (``body``: indented options lines)."""
    path = root / "catalog/sources.yaml"
    text = path.read_text()
    start = text.index("  - name: project-labels")
    end = text.index("\nenvironments_dir:")
    entry = "  - name: project-labels\n    type: project_labels\n" + ("" if enabled else "    enabled: false\n")
    path.write_text(text[:start] + entry + body + text[end:])


def _base(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO / "catalog", root / "catalog")
    (root / "contracts").mkdir()
    for name in ("catalog.schema.json", "catalog-snapshot.schema.json"):
        shutil.copy(REPO / "contracts" / name, root / "contracts")
    (root / "infrastructure/live").mkdir(parents=True)
    (root / "infrastructure/live/root.hcl").write_text("")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The repository catalog with the project-labels source enabled and the golden dev snapshot."""
    root = _base(tmp_path)
    _set_source(root, OPTIONS)
    (root / SNAPSHOT).parent.mkdir(parents=True)
    shutil.copy(GOLDEN, root / SNAPSHOT)
    return root


def _write(root: Path, rel: str, body: str) -> None:
    path = root / "catalog" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("schema: esmeralda.catalog\nschema_version: 1\n" + body)


def _problems(root: Path, env: str = "dev") -> str:
    with pytest.raises(CatalogError) as exc:
        catalog.resolve(root, env)
    return "\n".join(exc.value.problems)


def _labels(root: Path, alias: str, **labels: str) -> None:
    """Change the labels of one project in the committed snapshot (keeps it canonical)."""
    path = root / SNAPSHOT
    doc = json.loads(path.read_text())
    for entry in doc["entries"]:
        if entry["id"] == alias:
            entry["labels"].update(labels)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


# ------------------------------------------------------------------------------------------
# fetch: recorded responses -> sanitized, canonical snapshot
# ------------------------------------------------------------------------------------------


def test_fetch_writes_the_sanitized_golden_snapshot_and_is_idempotent(repo, live):
    (repo / SNAPSHOT).unlink()
    dry = commands.fetch_sources(repo, "dev", "project-labels", dry_run=True)["sources"][0]
    assert (dry["status"], dry["written"]) == ("created", False) and not (repo / SNAPSHOT).exists()

    result = commands.fetch_sources(repo, "dev", "project-labels")["sources"][0]
    text = (repo / SNAPSHOT).read_text()
    assert result["status"] == "created" and text == GOLDEN.read_text()
    assert result["added"] == ["ai_coe_agents", "cx_agents", "gateway", "mcps"]
    for literal in [*IDS.values(), *NUMBERS.values(), ORG, "agent_platform", "managed-by", "createTime"]:
        assert literal not in text
    assert snapshot_leaks(text) == []
    assert guard.scan_files(repo, [SNAPSHOT]) == []
    assert all(args[:2] == ["projects", "describe"] for args in live["calls"]) and len(live["calls"]) == 8

    again = commands.fetch_sources(repo, "dev", "project-labels")["sources"][0]
    assert again["status"] == "unchanged"


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_fetch_is_independent_of_the_response_order(repo, live, seed):
    rng = random.Random(seed)
    outputs = list(live["outputs"].items())
    rng.shuffle(outputs)
    live["outputs"] = dict(outputs)
    for response in live["describe"].values():
        items = list(response["labels"].items())
        rng.shuffle(items)
        response["labels"] = dict(items)
    assert commands.fetch_sources(repo, "dev", "project-labels")["sources"][0]["status"] == "unchanged"


def _projects(alias: str, output: str) -> str:
    return f'      projects:\n        - {{ alias: {alias}, output: "{output}" }}\n'


def test_fetch_reads_only_the_listed_projects(repo, live):
    _set_source(repo, OPTIONS + _projects("gateway", "layer-1-projects:gateway_project_id"))
    result = commands.fetch_sources(repo, "dev", "project-labels", dry_run=True)["sources"][0]
    assert result["removed"] == ["ai_coe_agents", "cx_agents", "mcps"] and live["calls"] == [
        ["projects", "describe", IDS["gateway"]]]

    _set_source(repo, OPTIONS + _projects("nope", "layer-1-projects:nope_project_id"))
    with pytest.raises(SourceUnreachable, match="no output 'nope_project_id'"):
        commands.fetch_sources(repo, "dev", "project-labels", dry_run=True)
    _set_source(repo, OPTIONS + "      projects:\n        - { alias: Bad, output: x }\n")
    with pytest.raises(CatalogError, match="alias must match"):
        commands.fetch_sources(repo, "dev", "project-labels", dry_run=True)


def test_fetch_fails_closed(repo, live, capsys):
    before = (repo / SNAPSHOT).read_text()
    live["unreachable"] = True
    args = ["catalog", "fetch", "--source", "project-labels", "--env", "dev", "--repo", str(repo)]
    assert cli.main(args) == 2
    assert "synthetic outage" in capsys.readouterr().err
    assert (repo / SNAPSHOT).read_text() == before

    live["unreachable"] = False
    live["outputs"] = {}
    with pytest.raises(SourceUnreachable, match="deploy layer 1 first"):
        commands.fetch_sources(repo, "dev", "project-labels")
    assert (repo / SNAPSHOT).read_text() == before


def test_a_label_value_that_is_an_environment_literal_is_never_written(repo, live):
    before = (repo / SNAPSHOT).read_text()
    live["describe"][IDS["gateway"]]["labels"]["team"] = "owner-" + "someone" + "@" + "example.com"
    with pytest.raises(CatalogError, match="email address"):
        commands.fetch_sources(repo, "dev", "project-labels")
    assert (repo / SNAPSHOT).read_text() == before


def test_a_disabled_source_can_be_fetched_before_it_is_enabled(tmp_path, live):
    root = _base(tmp_path)
    assert commands.fetch_sources(root, "dev", "all")["sources"] == []
    assert commands.fetch_sources(root, "dev", "project-labels")["sources"][0]["status"] == "created"


# ------------------------------------------------------------------------------------------
# resolve: attribution only
# ------------------------------------------------------------------------------------------


def test_labels_fill_team_projects_with_provenance(repo):
    resolved = catalog.resolve(repo, "dev")
    assert resolved.findings == []
    teams = resolved.teams
    assert teams["platform"]["projects"] == ["gateway", "mcps"]
    assert teams["ai-coe"]["projects"] == ["ai_coe_agents"] and teams["cx"]["projects"] == ["cx_agents"]
    assert teams["platform"]["cost_centers"] == ["cc-platform"], "labels never extend cost centres"
    assert resolved.provenance["teams.platform.projects"] == {
        "source": "project-labels", "origin": "snapshots/project-labels/dev.json#gateway,mcps", "strategy": "strict"}
    data = json.loads(catalog.render_resolved_file(resolved))
    assert data["attribution"]["project_teams"] == [
        {"project": "ai_coe_agents", "team": "ai-coe"}, {"project": "cx_agents", "team": "cx"},
        {"project": "gateway", "team": "platform"}, {"project": "mcps", "team": "platform"}]
    assert data["provenance"]["teams.platform.projects"]["source"] == "project-labels"


def test_labels_never_change_enforcement(repo):
    """Callers, gateway lists, registry entries and principal rows are byte-identical with labels on."""
    with_labels = json.loads(catalog.render_resolved_file(catalog.resolve(repo, "dev")))
    files_only = json.loads((REPO / "catalog/.resolved/dev.json").read_text())
    for block in COMPILED:
        assert with_labels[block] == files_only[block]
    for key in ("policy", "principal_teams", "shared_principals", "allowlists"):
        assert with_labels["attribution"][key] == files_only["attribution"][key]
    assert "project_teams" not in files_only["attribution"]


def test_resolve_is_deterministic(repo):
    first = catalog.render_resolved_file(catalog.resolve(repo, "dev"))
    assert catalog.render_resolved_file(catalog.resolve(repo, "dev")) == first
    assert json.dumps(catalog.resolve(repo, "dev").document) == json.dumps(catalog.resolve(repo, "dev").document)


def test_catalog_files_win_and_disagreements_are_findings(repo, capsys):
    teams = (REPO / "catalog/teams.yaml").read_text().split("schema_version: 1\n", 1)[1]
    _write(repo, "teams.yaml", teams.replace("cost_centers: [cc-cx-br, cc-cx-mx]",
                                             "cost_centers: [cc-cx-br, cc-cx-mx]\n    projects: [gateway]"))
    problems = _problems(repo)
    assert "project-labels: project gateway: teams.cx.projects lists it, but its label team=platform says 'platform'" \
        in problems
    assert "project cx_agents: label team=cx says 'cx', but teams.cx.projects (catalog files) doesn't list it" \
        in problems
    assert "label_mismatch is 'fail'" in problems and "catalog diff --env dev" in problems

    assert cli.main(["catalog", "diff", "--env", "dev", "--json", "--repo", str(repo)]) == 3
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert {(f["kind"], f["source"], f["subject"]) for f in findings} == {
        ("label_mismatch", "project-labels", "project gateway"),
        ("label_mismatch", "project-labels", "project cx_agents"),
    }
    resolved = catalog.resolve(repo, "dev", strict_findings=False)
    assert resolved.teams["cx"]["projects"] == ["gateway"], "files keep their value"
    assert resolved.teams["platform"]["projects"] == ["mcps"]


def test_unknown_team_and_foreign_cost_centre_are_findings(repo):
    _labels(repo, "gateway", team="netops")
    _labels(repo, "cx_agents", **{"cost-center": "cc-rogue"})
    problems = _problems(repo)
    assert "project gateway: label team=netops maps to team 'netops', which catalog files don't declare" in problems
    assert "project cx_agents: label cost-center=cc-rogue is not a cost centre of team 'cx'" in problems
    resolved = catalog.resolve(repo, "dev", strict_findings=False)
    assert "netops" not in resolved.teams, "labels never create teams"
    assert resolved.teams["cx"]["cost_centers"] == ["cc-cx-br", "cc-cx-mx"]


def test_unmapped_values_without_the_maps(repo):
    _set_source(repo)  # no team_map / cost_center_map
    problems = _problems(repo)
    assert "label team=appdev-tools maps to team 'appdev-tools'" in problems


def test_a_project_without_the_team_label_is_a_finding(repo):
    path = repo / SNAPSHOT
    doc = json.loads(path.read_text())
    doc["entries"][1]["labels"].pop("team")
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    assert "project cx_agents: has no 'team' label" in _problems(repo)


def test_label_mismatch_warn_only_in_flag_environments(repo, capsys):
    _labels(repo, "gateway", team="netops")
    _warn(repo, "dev")
    resolved = catalog.resolve(repo, "dev")
    assert [f["subject"] for f in resolved.findings] == ["project gateway"]
    assert cli.main(["catalog", "resolve", "--env", "dev", "--repo", str(repo)]) == 0
    assert "[label_mismatch] project-labels: project gateway" in capsys.readouterr().err

    _warn(repo, "prd")
    shutil.copy(repo / SNAPSHOT, repo / "catalog/snapshots/project-labels/prd.json")
    path = repo / "catalog/snapshots/project-labels/prd.json"
    path.write_text(path.read_text().replace('"environment": "dev"', '"environment": "prd"')
                    .replace("--env dev", "--env prd"))
    assert "label_mismatch: 'warn' is not allowed in environment 'prd'" in _problems(repo, "prd")


def _warn(root: Path, env: str) -> None:
    """Set policy.attribution.label_mismatch: warn in environments/<env>.yaml (keeping its overrides)."""
    path = root / f"catalog/environments/{env}.yaml"
    text = path.read_text().replace("\noverrides: {}\n", "\noverrides:\n")
    policy = "  policy:\n    attribution:\n      label_mismatch: warn\n"
    path.write_text(text.replace("\noverrides:\n", "\noverrides:\n" + policy, 1) if "\noverrides:\n" in text
                    else text + "overrides:\n" + policy)


def test_two_teams_may_not_list_the_same_project(repo):
    body = (REPO / "catalog/teams.yaml").read_text().split("schema_version: 1\n", 1)[1]
    body = body.replace("cost_centers: [cc-ai-coe]", "cost_centers: [cc-ai-coe]\n    projects: [shared]")
    body = body.replace("cost_centers: [cc-platform]", "cost_centers: [cc-platform]\n    projects: [shared]")
    _write(repo, "teams.yaml", body)
    assert "project 'shared' is also a project of team 'ai-coe'" in _problems(repo)


# ------------------------------------------------------------------------------------------
# owner_team of inventory and targets
# ------------------------------------------------------------------------------------------


@pytest.fixture
def inventory(monkeypatch, repo) -> Path:
    """A fake inventory source listed before project-labels, reporting two entries (synthetic)."""
    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))

    @register_source("fixture_registry")
    class FixtureRegistry(catalog_sources.Source):
        CAPABILITIES = {"inventory": ["*"]}

        def load(self) -> Layer:
            layer = Layer(source=self.name)
            layer.inventory["loan-helper"] = {
                "kind": "a2a", "runtime": "cloud_run",
                "runtime_resource": "//run.googleapis.com/projects/{project:ai_coe_agents}/locations/{region}"
                                    "/services/loan-helper"}
            layer.inventory["quote-bot"] = {
                "kind": "a2a", "owner_team": "platform",
                "runtime_resource": "//aiplatform.googleapis.com/projects/{project_number:cx_agents}/locations/"
                                    "{region}/reasoningEngines/1"}
            layer.inventory["orphan"] = {"kind": "http"}
            return layer

    path = repo / "catalog/sources.yaml"
    entry = "  - name: registry\n    type: fixture_registry\n"
    path.write_text(path.read_text().replace("  - name: project-labels", entry + "  - name: project-labels"))
    return repo


def test_labels_fill_owner_team_of_discovered_entries_only(inventory):
    resolved = catalog.resolve(inventory, "dev", strict_findings=False)
    assert resolved.inventory["loan-helper"]["owner_team"] == "ai-coe"
    assert resolved.provenance["inventory.loan-helper.owner_team"] == {
        "source": "project-labels", "origin": "snapshots/project-labels/dev.json#ai_coe_agents", "strategy": "strict"}
    assert "owner_team" not in resolved.inventory["orphan"]
    assert resolved.inventory["quote-bot"]["owner_team"] == "platform", "a reported owner is never overridden"
    assert [(f["subject"], f["detail"]) for f in resolved.findings] == [(
        "inventory.quote-bot",
        "owner_team is 'platform', but its project 'cx_agents' has the label team=cx ('cx')")]
    assert "inventory.quote-bot: owner_team is 'platform'" in _problems(inventory)


def test_target_owner_team_is_checked_against_its_project(repo):
    _write(repo, "teams/cx/agents/remote-helper.yaml", """\
name: remote-helper
kind: http
owner_team: cx
example: true
runtime: cloud_run
runtime_resource: //run.googleapis.com/projects/{project:gateway}/locations/{region}/services/remote-helper
deploy: { managed: false, endpoint: "https://remote-helper.example.com" }
""")
    assert ("targets.remote-helper: owner_team is 'cx', but its project 'gateway' has the label team=platform"
            in _problems(repo))


def test_project_alias_parsing():
    assert project_alias("//run.googleapis.com/projects/{project:gw}/locations/x") == "gw"
    assert project_alias("projects/{project_number:cx_agents}/x") == "cx_agents"
    assert project_alias("projects/literal/x") is None and project_alias(None) is None


# ------------------------------------------------------------------------------------------
# discovery never grants (9.1 capabilities still apply to this adapter)
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("section", "name", "value", "expected"), [
    ("targets", "legacy-dms", {"callers": {"test-vm": {}}}, "may not set targets.legacy-dms.callers"),
    ("teams", "platform", {"cost_centers": ["cc-rogue"]}, "may not set teams.platform.cost_centers"),
    ("teams", "netops", {"projects": ["gateway"]}, "may not create team 'netops'"),
    ("callers", "rogue", {"team": "platform"}, "may not set callers.rogue"),
    ("inventory", "new-thing", {"owner_team": "cx"}, "may not create inventory.new-thing"),
    ("inventory", "new-thing", {"kind": "http"}, "may not set inventory.new-thing.kind"),
])
def test_a_labels_adapter_can_never_set_authorization_data(repo, monkeypatch, section, name, value, expected):
    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))

    @register_source("project_labels")
    class Rogue(ProjectLabelsSource):
        def complete(self, layer: Layer, merged: Layer) -> Layer:
            layer = super().complete(layer, merged)
            layer.section(section)[name] = value
            return layer

    assert expected in _problems(repo)


# ------------------------------------------------------------------------------------------
# CLI and offline
# ------------------------------------------------------------------------------------------


def test_cli_show_provenance_lists_teams_and_their_projects(repo, capsys):
    args = ["catalog", "show", "--env", "dev", "--provenance", "--repo", str(repo)]
    assert cli.main([*args, "--json"]) == 0
    teams = {row["team"]: row for row in json.loads(capsys.readouterr().out)["teams"]}
    assert teams["platform"]["projects"] == ["gateway", "mcps"]
    assert teams["platform"]["provenance"]["fields"]["projects"]["source"] == "project-labels"
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert "👥 platform (cost centres cc-platform) projects=gateway, mcps" in out
    assert "projects: project-labels (snapshots/project-labels/dev.json#gateway,mcps, strict)" in out


def test_cli_sources_shows_the_snapshot(repo, capsys):
    assert cli.main(["catalog", "sources", "--env", "dev", "--json", "--repo", str(repo)]) == 0
    row = {r["name"]: r for r in json.loads(capsys.readouterr().out)["sources"]}["project-labels"]
    assert (row["enabled"], row["snapshot_status"], row["entries"]) == (True, "ok", 4)
    assert row["capabilities"] == {"inventory": ["owner_team"], "teams": ["projects"]}


def test_resolve_show_and_diff_open_no_socket(repo, monkeypatch):
    def no_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the catalog opened a network socket")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    args = ["--env", "dev", "--repo", str(repo)]
    assert cli.main(["catalog", "resolve", "--write", *args]) == 0
    assert cli.main(["catalog", "resolve", "--check", *args]) == 0
    assert cli.main(["catalog", "show", "--provenance", *args]) == 0
    assert cli.main(["catalog", "diff", *args]) == 0


def test_shipped_source_is_disabled_and_the_golden_snapshot_passes_the_guard():
    rows = {r["name"]: r for r in commands.describe_sources(REPO, "dev")["sources"]}
    assert rows["project-labels"]["enabled"] is False and rows["project-labels"]["type"] == "project_labels"
    assert guard.scan_files(REPO, [GOLDEN.relative_to(REPO).as_posix()]) == []
