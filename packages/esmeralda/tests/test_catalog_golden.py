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

"""Golden multi-source catalog: ``files`` + ``agent_registry`` + ``backstage`` + ``project_labels``.

The fixture under ``tests/fixtures/catalog_golden/`` is a small, self-contained, synthetic catalog
with a committed snapshot per external source. Resolving it must give, byte for byte, the
committed ``resolved.dev.json`` (``esmeralda catalog resolve --env dev`` output) and
``catalog/.resolved/dev.json`` (``--write`` output). It exercises in one place: discovered
inventory, adoption by pin (``deploy.registry``, ``backstage``, and both on one entry), field
strategies (``strict``, ``first`` with shadowed losers, an environment ``override``), team profiles
from Backstage Groups, project-label attribution, and the invariants (no grant, team or cost
centre from outside the catalog files; discovered entries never compiled; offline; deterministic).

After an intended change, regenerate the golden files and review the diff::

    uv run --frozen python packages/esmeralda/tests/test_catalog_golden.py --update
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from esmeralda import catalog, cli
from esmeralda.catalog import commands
from esmeralda.ops import guard

REPO = Path(__file__).resolve().parents[3]
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "catalog_golden"
RESOLVED_JSON = GOLDEN / "resolved.dev.json"
RESOLVED_FILE = GOLDEN / "catalog" / ".resolved" / "dev.json"
SNAPSHOTS = ("agent-registry", "backstage", "project-labels")


def _repo(root: Path) -> Path:
    """A repository tree: the golden catalog plus the contracts it is validated against."""
    shutil.copytree(GOLDEN / "catalog", root / "catalog")
    (root / "contracts").mkdir(parents=True)
    for name in ("catalog.schema.json", "catalog-snapshot.schema.json"):
        shutil.copy(REPO / "contracts" / name, root / "contracts")
    (root / "infrastructure/live").mkdir(parents=True)
    (root / "infrastructure/live/root.hcl").write_text("")
    return root


def _cli(*args: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def render(root: Path) -> tuple[str, str]:
    """``(catalog resolve --env dev output, catalog/.resolved/dev.json content)`` for ``root``."""
    code, text = _cli("catalog", "resolve", "--env", "dev", "--repo", str(root))
    assert code == 0, text
    return text, catalog.render_resolved_file(catalog.resolve(root, "dev"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _repo(tmp_path / "repo")


# ------------------------------------------------------------------------------------------
# done when: byte equality with the committed golden files
# ------------------------------------------------------------------------------------------


def test_resolve_matches_the_golden_files_byte_for_byte(repo):
    resolved_json, resolved_file = render(repo)
    assert resolved_json == RESOLVED_JSON.read_text(), "resolved.dev.json is stale: see the module docstring"
    assert resolved_file == RESOLVED_FILE.read_text(), "catalog/.resolved/dev.json is stale"


def test_resolve_check_passes_on_the_golden_tree(repo):
    assert _cli("catalog", "resolve", "--env", "all", "--check", "--repo", str(repo))[0] == 0


def test_every_source_contributes(repo):
    doc = json.loads(RESOLVED_JSON.read_text())
    assert [s["name"] if isinstance(s, dict) else s for s in doc["sources"]] == [
        "catalog-files", "agent-registry", "backstage", "project-labels", "environments/dev.yaml"]
    by_source: dict[str, set[str]] = {}
    for key, record in doc["provenance"].items():
        by_source.setdefault(record["source"], set()).add(key)
        for loser in record.get("shadowed") or []:
            by_source.setdefault(loser["source"], set()).add(key)
    assert set(by_source) == {"catalog-files", "agent-registry", "backstage", "project-labels",
                              "environments/dev.yaml"}


def test_golden_shows_each_merge_rule(repo):
    resolved = catalog.resolve(repo, "dev")
    prov = resolved.provenance
    # discovered inventory, owner filled by project labels
    assert sorted(resolved.inventory) == ["pricing-tools", "quote-bot", "rates-model"]
    assert resolved.inventory["quote-bot"]["owner_team"] == "cx"
    assert prov["inventory.quote-bot.owner_team"]["source"] == "project-labels"
    # one catalog entry joins two sources; first-wins description, Backstage shadowed
    assert resolved.adopted_by == {"ai-coe-mortgage-specialist": ["backstage"],
                                   "loan-helper": ["agent-registry", "backstage"], "pricing-api": ["backstage"]}
    assert prov["targets.loan-helper.description"]["source"] == "agent-registry"
    assert prov["targets.loan-helper.description"]["shadowed"][0]["source"] == "backstage"
    assert prov["targets.loan-helper.system"]["source"] == "backstage"
    assert prov["targets.loan-helper.runtime"]["strategy"] == "strict"
    # the catalog file wins over Backstage; a deployed target never takes an endpoint
    assert prov["targets.ai-coe-mortgage-specialist.description"]["source"] == "catalog-files"
    assert "endpoint" not in resolved.targets["ai-coe-mortgage-specialist"]["deploy"]
    # the environment override beats the source
    assert prov["targets.pricing-api.description"]["strategy"] == "override"
    # team profiles: files win, Backstage fills the rest; teams and cost centres untouched
    assert prov["profiles.ai-coe.oncall"]["shadowed"][0]["source"] == "backstage"
    assert resolved.document["profiles"]["cx"]["oncall"] == "cx-pager"
    assert resolved.teams["cx"]["cost_centers"] == ["cc-cx-br"]
    assert resolved.teams["ai-coe"]["projects"] == ["ai_coe_agents"]


def test_no_grant_team_or_cost_centre_comes_from_outside_the_catalog_files(repo):
    resolved = catalog.resolve(repo, "dev")
    for key, record in resolved.provenance.items():
        section, name, *path = key.split(".", 2)
        if record["source"] in ("catalog-files", "environments/dev.yaml"):
            continue
        assert section != "callers", key
        assert not (path and path[0].split(".")[0] in ("callers", "identity", "google_apis", "cost_centers")), key
        assert not (section == "teams" and not path), f"{key}: a source created a team"
    data = json.loads(RESOLVED_FILE.read_text())
    principals = {row["caller"] for row in data["attribution"]["principal_teams"]}
    assert principals <= {"ai-coe-mortgage-specialist", "cx-mortgage-orchestrator", "test-vm"}


def test_discovered_entries_are_never_compiled(repo):
    data = json.loads(RESOLVED_FILE.read_text())
    text = json.dumps({k: data[k] for k in ("catalog", "external_targets", "google_apis", "iap_egress",
                                            "attribution")})
    for name in ("pricing-tools", "quote-bot", "rates-model"):
        assert name not in text
    assert data["inventory_summary"] == {
        "agent-registry": {"adopted": 1, "discovered": 2},
        "backstage": {"adopted": 3, "discovered": 1},
    }


def test_diff_lists_discovered_shadowed_overridden_and_suggestions(repo):
    data = commands.diff_catalog(repo, "dev")
    kinds = {(f["kind"], f["subject"]) for f in data["findings"]}
    assert ("discovered", "inventory.quote-bot") in kinds
    assert ("shadowed", "targets.loan-helper.description") in kinds
    assert ("overridden", "targets.pricing-api.description") in kinds
    assert ("suggested_grant", "loan-helper") in kinds, "consumesApis is a hint, never a caller"
    assert "loan-helper" not in (catalog.resolve(repo, "dev").targets["pricing-api"].get("callers") or {})


def test_snapshots_pass_the_leak_guard(repo):
    paths = [f"catalog/snapshots/{name}/dev.json" for name in SNAPSHOTS]
    assert guard.scan_files(repo, paths) == []
    for name in SNAPSHOTS:
        src = next(s for _, s in commands._all_sources(repo, "dev") if getattr(s, "name", None) == name)
        assert src.read() is not None  # schema, canonical form, placeholders, leaks


def test_resolve_is_offline(repo, monkeypatch):
    def no_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the catalog opened a network socket")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    assert render(repo) == (RESOLVED_JSON.read_text(), RESOLVED_FILE.read_text())


@pytest.mark.parametrize("seed", ["0", "1", "4242"])
def test_resolve_is_independent_of_the_hash_seed(repo, seed):
    env = {**os.environ, "PYTHONHASHSEED": seed}
    result = subprocess.run([sys.executable, "-m", "esmeralda.cli", "catalog", "resolve", "--env", "dev", "--repo",
                             str(repo)], capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == RESOLVED_JSON.read_text()


def test_repository_resolved_files_are_untouched():
    for env in catalog.resolver.environments(REPO):
        ok, message = catalog.check_resolved(REPO, env)
        assert ok, message


def _update(tmp: Path) -> None:
    resolved_json, resolved_file = render(_repo(tmp / "repo"))
    RESOLVED_JSON.write_text(resolved_json)
    RESOLVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESOLVED_FILE.write_text(resolved_file)
    print(f"wrote {RESOLVED_JSON.relative_to(REPO)} and {RESOLVED_FILE.relative_to(REPO)}")


if __name__ == "__main__":  # pragma: no cover - maintenance entry point
    import tempfile

    if sys.argv[1:] != ["--update"]:
        sys.exit("usage: python packages/esmeralda/tests/test_catalog_golden.py --update")
    with tempfile.TemporaryDirectory() as tmp:
        _update(Path(tmp))
