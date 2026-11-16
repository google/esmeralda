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

"""Multi-source catalog merging: capabilities, strategies, adoption, provenance, determinism.

Everything here is synthetic: fake adapters registered for the test, no network, no real ids.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from esmeralda import catalog
from esmeralda.catalog import CatalogError, Layer, Source, register_source
from esmeralda.catalog import sources as catalog_sources

REPO = Path(__file__).resolve().parents[3]
HEADER = "schema: esmeralda.catalog\nschema_version: 1\n"
COMPILED_BLOCKS = ("catalog", "external_targets", "google_apis", "iap_egress", "attribution")

# A catalog file target adopted from the source named "registry" by an explicit pin (synthetic).
PINNED_TARGET = """\
name: pricing-api
kind: http
owner_team: ai-coe
deploy:
  managed: false
  endpoint: https://pricing.example.com
  registry: { source: registry, id: endpoints/pricing }
"""
REGISTRY_INVENTORY = {
    "pricing": {
        "kind": "http",
        "description": "Pricing API (from the registry)",
        "runtime": "cloud_run",
        "deploy": {"endpoint": "https://pricing.example.com"},
        "registry": {"id": "endpoints/pricing", "skills": ["quote"]},
    },
    "loan-helper": {"kind": "a2a", "description": "Discovered agent", "registry": {"id": "agents/loan-helper"}},
}

_UNSET = object()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A copy of the repository's catalog/ and contracts/ to mutate."""
    shutil.copytree(REPO / "catalog", tmp_path / "catalog")
    (tmp_path / "contracts").mkdir()
    shutil.copy(REPO / "contracts" / "catalog.schema.json", tmp_path / "contracts")
    return tmp_path


@pytest.fixture
def adapter(monkeypatch) -> Callable[..., type[Source]]:
    """Register a fake adapter: ``adapter(type_name, build)`` where ``build(layer, source)`` fills the layer."""
    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))

    def make(type_name: str, build: Callable[[Layer, Source], None], capabilities: Any = _UNSET) -> type[Source]:
        class Fake(Source):
            def load(self) -> Layer:
                layer = Layer(source=self.name)
                build(layer, self)
                return layer

        if capabilities is not _UNSET:
            Fake.CAPABILITIES = capabilities
        return register_source(type_name)(Fake)

    return make


def _write(root: Path, rel: str, body: str) -> None:
    path = root / "catalog" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + body)


def _add_source(root: Path, name: str, type_name: str, options: dict[str, Any] | None = None) -> None:
    path = root / "catalog/sources.yaml"
    entry = f"  - name: {name}\n    type: {type_name}\n"
    if options is not None:
        entry += f"    options: {json.dumps(options)}\n"
    path.write_text(path.read_text().replace("\n\nenvironments_dir:", f"\n{entry}\nenvironments_dir:"))


def _problems(root: Path, env: str = "dev") -> str:
    with pytest.raises(CatalogError) as exc:
        catalog.resolve(root, env)
    return "\n".join(exc.value.problems)


def _compiled(root: Path, env: str = "dev") -> dict[str, Any]:
    data = json.loads(catalog.render_resolved_file(catalog.resolve(root, env)))
    return {block: data[block] for block in COMPILED_BLOCKS}


def _registry(layer: Layer, _source: Source) -> None:
    for name, entry in REGISTRY_INVENTORY.items():
        layer.inventory[name] = json.loads(json.dumps(entry))
        layer.origins[("inventory", name)] = f"snapshots/registry/dev.json#{entry['registry']['id']}"


# ------------------------------------------------------------------------------------------
# the repository catalog: a no-op
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env", ["dev", "prd"])
def test_files_only_catalog_resolved_file_is_byte_identical(env):
    path = REPO / "catalog/.resolved" / f"{env}.json"
    assert catalog.render_resolved_file(catalog.resolve(REPO, env)) == path.read_text()
    data = json.loads(path.read_text())
    assert "provenance" not in data and "inventory_summary" not in data


def test_files_only_provenance_is_entry_level_from_files():
    resolved = catalog.resolve(REPO, "dev")
    assert resolved.inventory == {} and resolved.adopted == {}
    assert resolved.provenance["targets.legacy-dms"] == {
        "source": "catalog-files", "origin": "catalog/teams/ai-coe/mcp/legacy-dms.yaml",
    }
    assert all(key.count(".") == 1 for key in resolved.provenance), "files-only: no field-level provenance"
    assert {r["source"] for r in resolved.provenance.values()} <= {"catalog-files", "environments/dev.yaml"}


# ------------------------------------------------------------------------------------------
# I1 / I2: no grant, team or cost centre from outside Git (capability allowlist)
# ------------------------------------------------------------------------------------------


def _inject(section: str, name: str, value: Any) -> Callable[[Layer, Source], None]:
    def build(layer: Layer, _source: Source) -> None:
        if section == "policy":
            layer.policy = value
        else:
            layer.section(section)[name] = value

    return build


INJECTIONS = [
    # (source type, section, name, value, expected problem)
    ("agent_registry", "targets", "legacy-dms", {"callers": {"rogue": {}}},
     "may not set targets.legacy-dms.callers (authorization data"),
    ("agent_registry", "targets", "ai-coe-mortgage-specialist",
     {"callers": {"cx-mortgage-orchestrator": {"cost_centers": ["cc-platform"]}}},
     "may not set targets.ai-coe-mortgage-specialist.callers"),
    ("agent_registry", "targets", "legacy-dms", {"identity": {"type": "service_account", "id": "x"}},
     "may not set targets.legacy-dms.identity"),
    ("agent_registry", "targets", "corporate-email", {"user_credential": {"accept": True}},
     "may not set targets.corporate-email.user_credential"),
    ("agent_registry", "targets", "legacy-dms", {"google_apis": ["bigquery"]},
     "may not set targets.legacy-dms.google_apis"),
    ("agent_registry", "callers", "rogue",
     {"team": "platform", "identity": {"type": "service_account", "id": "rogue"}}, "may not set callers.rogue"),
    ("agent_registry", "policy", "", {"attribution": {"unmapped_caller": "flag"}}, "may not set policy"),
    ("agent_registry", "policy", "", {"agent_gateway": {"shared_workload_identity": {"ref": "a:b", "kinds": ["mcp"]}}},
     "may not set policy"),
    ("project_labels", "teams", "rogue", {"projects": ["p"]}, "may not create team 'rogue'"),
    ("project_labels", "teams", "platform", {"cost_centers": ["cc-rogue"]},
     "may not set teams.platform.cost_centers (authorization data"),
    ("agent_registry", "inventory", "rogue-agent", {"kind": "a2a", "identity": {"type": "group", "id": "g"}},
     "may not set inventory.rogue-agent.identity"),
    ("agent_registry", "inventory", "rogue-agent", {"kind": "mcp", "deploy": {"unit": "services/rogue-agent"}},
     "may not set inventory.rogue-agent.deploy.unit"),
    ("agent_registry", "inventory", "rogue-agent", {"kind": "a2a", "callers": {"test-vm": {}}},
     "may not set inventory.rogue-agent.callers"),
    ("agent_registry", "inventory", "rogue-agent", {"kind": "a2a", "google_apis": ["storage"]},
     "may not set inventory.rogue-agent.google_apis"),
    ("agent_registry", "inventory", "rogue-agent", {"kind": "a2a", "user_credential": {"accept": True}},
     "may not set inventory.rogue-agent.user_credential"),
    ("agent_registry", "profiles", "ai-coe", {"oncall": "x"}, "may not set profiles.ai-coe.oncall"),
]


@pytest.mark.parametrize(("type_name", "section", "name", "value", "expected"), INJECTIONS,
                         ids=[f"{t}-{s}-{e.split(' (')[0]}" for t, s, _, _, e in INJECTIONS])
def test_external_source_may_not_add_or_alter_authorization_data(repo, adapter, type_name, section, name, value,
                                                                 expected):
    adapter(type_name, _inject(section, name, value))
    _add_source(repo, "rogue-source", type_name)
    assert f"rogue-source: {expected}" in _problems(repo)


def test_custom_adapter_without_capabilities_contributes_nothing(repo, adapter):
    adapter("cmdb", _inject("inventory", "thing", {"kind": "http"}))
    _add_source(repo, "cmdb", "cmdb")
    assert "cmdb: may not set inventory.thing.kind (source type capabilities for inventory: nothing)" in _problems(repo)


def test_custom_adapter_capabilities_are_capped(repo, adapter):
    adapter("cmdb", _inject("inventory", "thing", {"kind": "http"}), capabilities={"targets": ["callers"]})
    _add_source(repo, "cmdb", "cmdb")
    assert "capability 'targets.callers' is beyond what this source type may contribute" in _problems(repo)


def test_builtin_type_capabilities_cannot_be_widened_by_the_adapter_class(repo, adapter):
    adapter("project_labels", _inject("teams", "platform", {"projects": ["p"]}),
            capabilities={"teams": ["projects", "cost_centers"]})
    _add_source(repo, "labels", "project_labels")
    assert "capability 'teams.cost_centers' is beyond" in _problems(repo)


def test_options_fields_narrow_but_never_widen(repo, adapter):
    """I9: sources.yaml can't grant a source capabilities beyond its code constant."""
    adapter("agent_registry", _registry)
    _add_source(repo, "registry", "agent_registry", {"fields": ["targets.callers"]})
    assert "options.fields entry 'targets.callers' is beyond" in _problems(repo)

    repo2 = repo.parent / "narrow"
    shutil.copytree(repo, repo2)
    text = (repo2 / "catalog/sources.yaml").read_text().replace('["targets.callers"]', '["targets.description"]')
    (repo2 / "catalog/sources.yaml").write_text(text)
    assert "registry: may not set inventory.loan-helper.description (source type capabilities for inventory: " \
           "nothing)" in _problems(repo2)


def test_files_type_is_reserved_for_the_builtin_source(adapter):
    with pytest.raises(CatalogError, match="reserved"):
        adapter("files", _inject("targets", "legacy-dms", {}))


def test_first_enabled_source_must_be_files(repo, adapter):
    adapter("agent_registry", _registry)
    path = repo / "catalog/sources.yaml"
    path.write_text(path.read_text().replace(
        "sources:\n", "sources:\n  - name: registry\n    type: agent_registry\n", 1))
    assert "the first enabled source must be of type files" in _problems(repo)


# ------------------------------------------------------------------------------------------
# I3 / I4: discovered entries are never compiled; adoption by pin only
# ------------------------------------------------------------------------------------------


def test_discovered_inventory_is_never_compiled(repo, adapter):
    baseline = _compiled(repo)
    adapter("agent_registry", _registry)
    _add_source(repo, "registry", "agent_registry")
    resolved = catalog.resolve(repo, "dev")
    assert set(resolved.inventory) == {"pricing", "loan-helper"}
    assert resolved.provenance["inventory.loan-helper"]["source"] == "registry"
    assert _compiled(repo) == baseline
    data = json.loads(catalog.render_resolved_file(resolved))
    assert data["inventory_summary"] == {"registry": {"adopted": 0, "discovered": 2}}


def test_pin_adopts_and_fills_unset_inventory_fields(repo, adapter):
    unpinned = "\n".join(line for line in PINNED_TARGET.splitlines() if "registry:" not in line) + "\n"
    # Without the pin nothing fills the description, which every target needs.
    _write(repo, "external/legacy/pricing-api.yaml", unpinned + "description: Pricing API (from the registry)\n")
    baseline = _compiled(repo)
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET)
    adapter("agent_registry", _registry)
    _add_source(repo, "registry", "agent_registry")
    resolved = catalog.resolve(repo, "dev")

    target = resolved.targets["pricing-api"]
    assert target["description"] == "Pricing API (from the registry)"
    assert target["runtime"] == "cloud_run"
    assert target["registry"] == {"id": "endpoints/pricing", "skills": ["quote"]}
    assert "pricing" not in resolved.inventory and set(resolved.inventory) == {"loan-helper"}
    assert resolved.adopted == {"pricing-api": "registry"}
    origin = "snapshots/registry/dev.json#endpoints/pricing"
    assert resolved.provenance["targets.pricing-api.runtime"] == {
        "source": "registry", "origin": origin, "strategy": "strict"}
    assert resolved.provenance["targets.pricing-api.description"]["strategy"] == "first"
    assert resolved.provenance["targets.pricing-api.registry.skills"]["strategy"] == "first"

    compiled = _compiled(repo)
    for block in ("catalog", "iap_egress", "attribution", "google_apis"):
        assert compiled[block] == baseline[block], block  # adoption grants nothing by itself
    assert compiled["external_targets"]["pricing-api"]["description"] == (
        "Pricing API (from the registry). Used by: no caller declared.")
    data = json.loads(catalog.render_resolved_file(resolved))
    assert data["provenance"]["targets.pricing-api.runtime"]["source"] == "registry"
    assert data["inventory_summary"] == {"registry": {"adopted": 1, "discovered": 1}}


def test_name_match_without_pin_is_an_error(repo, adapter):
    adapter("agent_registry", _inject("inventory", "legacy-dms", {"kind": "mcp", "description": "squat"}))
    _add_source(repo, "registry", "agent_registry")
    assert "registry: inventory.legacy-dms has the name of catalog target 'legacy-dms'" in _problems(repo)


def test_pinned_id_missing_from_the_source_is_an_error(repo, adapter):
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET.replace("endpoints/pricing", "endpoints/gone"))
    adapter("agent_registry", _registry)
    _add_source(repo, "registry", "agent_registry")
    assert "pins id 'endpoints/gone' of source 'registry', which it doesn't report" in _problems(repo)


def test_pin_to_a_disabled_or_files_source_is_an_error(repo):
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET)
    assert "deploy.registry pins source 'registry', which is not an enabled source" in _problems(repo)
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET.replace("source: registry", "source: catalog-files"))
    assert "which is a catalog files source" in _problems(repo)


def test_two_sources_deriving_the_same_name_is_an_error(repo, adapter):
    adapter("agent_registry", _registry)
    adapter("backstage", _inject("inventory", "loan-helper", {"kind": "a2a"}))
    _add_source(repo, "registry", "agent_registry")
    _add_source(repo, "backstage", "backstage")
    assert "backstage: inventory.loan-helper: also reported by registry" in _problems(repo)


# ------------------------------------------------------------------------------------------
# field strategies
# ------------------------------------------------------------------------------------------


def test_strict_conflict_between_files_and_source_fails_with_both_origins(repo, adapter):
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET + "runtime: gke\n")
    adapter("agent_registry", _registry)
    _add_source(repo, "registry", "agent_registry")
    problems = _problems(repo)
    assert "targets.pricing-api.runtime: registry (snapshots/registry/dev.json#endpoints/pricing) reports" in problems
    assert "catalog-files (catalog/external/legacy/pricing-api.yaml) has 'gke'" in problems


def test_adopted_target_kind_must_agree_even_if_the_source_cannot_fill_it(repo, adapter):
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET.replace("kind: http", "kind: model"))
    adapter("agent_registry", _registry)
    _add_source(repo, "registry", "agent_registry")
    assert "targets.pricing-api.kind: registry" in _problems(repo)


def test_first_keeps_the_files_value_and_records_the_shadowed_one(repo, adapter):
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET + "description: From the catalog file\n")
    adapter("agent_registry", _registry)
    _add_source(repo, "registry", "agent_registry")
    resolved = catalog.resolve(repo, "dev")
    assert resolved.targets["pricing-api"]["description"] == "From the catalog file"
    record = resolved.provenance["targets.pricing-api.description"]
    assert record["source"] == "catalog-files" and record["strategy"] == "first"
    assert record["shadowed"][0]["source"] == "registry" and len(record["shadowed"][0]["value_hash"]) == 16


def test_two_externals_earlier_wins_on_first_and_conflict_on_strict(repo, adapter):
    def profile(text: str) -> Callable[[Layer, Source], None]:
        return _inject("profiles", "cx", {"oncall": text})

    adapter("backstage", profile("cx-oncall-a"))
    adapter("backstage_two", profile("cx-oncall-b"), capabilities={"profiles": ["oncall"]})
    _add_source(repo, "backstage", "backstage")
    _add_source(repo, "backstage-two", "backstage_two")
    resolved = catalog.resolve(repo, "dev")
    assert resolved.document["profiles"]["cx"] == {"team": "cx", "oncall": "cx-oncall-a"}
    record = resolved.provenance["profiles.cx.oncall"]
    assert record["source"] == "backstage" and record["shadowed"][0]["source"] == "backstage-two"

    repo2 = repo.parent / "strict"
    shutil.copytree(repo, repo2)
    adapter("project_labels", _inject("teams", "platform", {"projects": ["shared-a"]}))
    adapter("labels_two", _inject("teams", "platform", {"projects": ["shared-b"]}),
            capabilities={"teams": ["projects"]})
    _add_source(repo2, "labels", "project_labels")
    _add_source(repo2, "labels-two", "labels_two")
    assert "teams.platform.projects: labels-two" in _problems(repo2)


def test_labels_fill_discovered_owner_team_but_never_create_entries(repo, adapter):
    adapter("agent_registry", _registry)
    adapter("project_labels", _inject("inventory", "loan-helper", {"owner_team": "ai-coe"}))
    _add_source(repo, "registry", "agent_registry")
    _add_source(repo, "labels", "project_labels")
    resolved = catalog.resolve(repo, "dev")
    assert resolved.inventory["loan-helper"]["owner_team"] == "ai-coe"
    assert resolved.provenance["inventory.loan-helper.owner_team"]["source"] == "labels"

    repo2 = repo.parent / "create"
    shutil.copytree(repo, repo2)
    adapter("project_labels", _inject("inventory", "nobody-reported", {"owner_team": "ai-coe"}))
    assert "labels: may not create inventory.nobody-reported" in _problems(repo2)


def test_environment_override_beats_an_external_strict_field(repo, adapter):
    """I11: environments/<env>.yaml (repo-reviewed) is the way to override a strict external field."""
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET)
    adapter("agent_registry", _registry)
    _add_source(repo, "registry", "agent_registry")
    path = repo / "catalog/environments/dev.yaml"
    text = path.read_text()
    assert "  targets:\n" in text
    path.write_text(text.replace("  targets:\n", "  targets:\n    pricing-api:\n      runtime: gke\n", 1))
    resolved = catalog.resolve(repo, "dev")
    assert resolved.targets["pricing-api"]["runtime"] == "gke"
    record = resolved.provenance["targets.pricing-api.runtime"]
    assert record["source"] == "environments/dev.yaml" and record["strategy"] == "override"
    assert record["shadowed"][0]["source"] == "registry"


def test_label_mismatch_warn_is_refused_outside_flag_environments(repo):
    path = repo / "catalog/policy.yaml"
    text = path.read_text()
    assert "  attribution:\n" in text
    path.write_text(text.replace("  attribution:\n", "  attribution:\n    label_mismatch: warn\n", 1))
    catalog.resolve(repo, "dev")
    assert "policy.attribution.label_mismatch: 'warn' is not allowed in environment 'prd'" in _problems(repo, "prd")


# ------------------------------------------------------------------------------------------
# I7: determinism
# ------------------------------------------------------------------------------------------


def _shuffled(seed: int) -> Callable[[Layer, Source], None]:
    def build(layer: Layer, _source: Source) -> None:
        rng = random.Random(seed)
        names = [f"svc-{i:02d}" for i in range(12)]
        rng.shuffle(names)
        for name in names:
            entry = {"kind": "http", "description": name, "registry": {"id": f"endpoints/{name}", "skills": ["a"]}}
            keys = list(entry)
            rng.shuffle(keys)
            layer.inventory[name] = {k: entry[k] for k in keys}
        _registry(layer, _source)

    return build


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_permuted_adapter_output_resolves_to_identical_bytes(repo, adapter, seed):
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET)
    _add_source(repo, "registry", "agent_registry")
    adapter("agent_registry", _shuffled(99))
    reference = catalog.resolve(repo, "dev")
    expected = (json.dumps(reference.document), catalog.render_resolved_file(reference))
    adapter("agent_registry", _shuffled(seed))
    for _ in range(2):  # and a re-run
        again = catalog.resolve(repo, "dev")
        assert (json.dumps(again.document), catalog.render_resolved_file(again)) == expected


HASHSEED_SCRIPT = """
import json, sys
from pathlib import Path
from esmeralda import catalog
from esmeralda.catalog import Layer, Source, register_source

@register_source("agent_registry")
class Registry(Source):
    def load(self):
        layer = Layer(source=self.name)
        for name in {"svc-a", "svc-b", "svc-c", "svc-d", "svc-e", "pricing"}:  # set: hash-seed order
            entry_id = "endpoints/pricing" if name == "pricing" else "endpoints/" + name
            layer.inventory[name] = {"kind": "http", "description": name, "registry": {"id": entry_id}}
        return layer

resolved = catalog.resolve(Path(sys.argv[1]), "dev")
sys.stdout.write(json.dumps(resolved.document) + "\\n" + catalog.render_resolved_file(resolved))
"""


def test_resolve_is_independent_of_the_hash_seed(repo, tmp_path):
    _write(repo, "external/legacy/pricing-api.yaml", PINNED_TARGET)
    _add_source(repo, "registry", "agent_registry")
    script = tmp_path / "resolve_with_adapter.py"
    script.write_text(HASHSEED_SCRIPT)
    outputs = set()
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        run = subprocess.run([sys.executable, str(script), str(repo)], env=env, capture_output=True, text=True,
                             check=False)
        assert run.returncode == 0, run.stderr
        outputs.add(run.stdout)
    assert len(outputs) == 1
