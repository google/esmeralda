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

"""Adoption compile (Phase 9.4b): a catalog file adopts a discovered Agent Registry entry by pin.

An adopted Agent Runtime agent compiles into a governance registry entry on URLs derived from its
engine resource (``adopted_targets``), the per-target caller list on it (``iap_egress.agents``) and,
when it is itself a caller, a derived principal (``principal_teams``). Covers invariants I1 (no grant
from outside Git), I3 (discovered => not compiled), I4 (adoption by pin only), I7 (determinism) and
I10 (allowed => attributable), strict field equality, a missing pin and unsupported runtimes.

Everything is synthetic: a hand-written ``agent-registry`` snapshot with placeholders only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from esmeralda import catalog
from esmeralda.catalog import CatalogError, engine_urls, runtime_principal

REPO = Path(__file__).resolve().parents[3]
SNAPSHOT = "catalog/snapshots/agent-registry/dev.json"
UNCHANGED = ("catalog", "external_targets", "google_apis", "attribution")

ENGINE = "5550001"
RESOURCE = f"//aiplatform.googleapis.com/projects/{{project_number:ai_coe_agents}}/locations/{{region}}/reasoningEngines/{ENGINE}"
LOAN_ID = ("urn:agent:projects-{project_number:ai_coe_agents}:projects:{project_number:ai_coe_agents}:locations:"
           f"{{region}}:aiplatform:reasoningEngines:{ENGINE}")
QUERY_URL = (f"https://{{region}}-aiplatform.googleapis.com/v1/projects/{{project_number:ai_coe_agents}}/locations/"
             f"{{region}}/reasoningEngines/{ENGINE}:query")
QUOTE_ID = "urn:agent:projects-{project_number:ai_coe_agents}:projects:{project_number:ai_coe_agents}:locations:" \
           "{region}:run:services:quote-bot"
ORCHESTRATOR = "agents/cx-mortgage-orchestrator:agent_identity_principal"
ADOPTED = "adopted/loan-pricing:agent_identity_principal"


def _entries() -> list[dict[str, Any]]:
    return [
        {"id": LOAN_ID, "name": "loan-helper", "inventory": {
            "kind": "a2a",
            "description": "Loan pricing agent deployed by another team (synthetic).",
            "deploy": {"endpoint": QUERY_URL},
            "runtime": "agent_runtime",
            "runtime_resource": RESOURCE,
            "registry": {"id": LOAN_ID, "interfaces": [{"url": QUERY_URL, "protocol_binding": "HTTP_JSON"}]},
        }},
        {"id": QUOTE_ID, "name": "quote-bot", "inventory": {
            "kind": "a2a",
            "description": "Quote bot on Cloud Run (synthetic).",
            "deploy": {"endpoint": "https://quote-bot.{domain}"},
            "runtime": "cloud_run",
            "runtime_resource": "//run.googleapis.com/projects/{project:ai_coe_agents}/locations/{region}/services/quote-bot",
            "registry": {"id": QUOTE_ID},
        }},
    ]


def _snapshot(entries: list[dict[str, Any]]) -> str:
    doc = {
        "schema": "esmeralda.catalog.snapshot", "schema_version": 1, "source": "agent-registry",
        "type": "agent_registry", "environment": "dev",
        "fetched_by": "esmeralda catalog fetch --env dev --source agent-registry", "entries": entries,
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The repository catalog with the agent-registry source enabled and a synthetic dev snapshot."""
    root = tmp_path / "repo"
    shutil.copytree(REPO / "catalog", root / "catalog")
    (root / "contracts").mkdir()
    for name in ("catalog.schema.json", "catalog-snapshot.schema.json"):
        shutil.copy(REPO / "contracts" / name, root / "contracts")
    path = root / "catalog/sources.yaml"
    text = path.read_text()
    entry = "  - name: agent-registry\n    type: agent_registry\n    enabled: false\n    options:\n"
    assert entry in text
    # optional: the prd environment has no snapshot (adoption makes dev's mandatory).
    path.write_text(text.replace(entry, entry.replace("    enabled: false\n", "") + "      optional: true\n"))
    (root / SNAPSHOT).parent.mkdir(parents=True)
    (root / SNAPSHOT).write_text(_snapshot(_entries()))
    return root


def _write(root: Path, rel: str, body: str) -> None:
    path = root / "catalog" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("schema: esmeralda.catalog\nschema_version: 1\n" + body)


ADOPTION = f"""\
name: loan-pricing
kind: a2a
owner_team: ai-coe
deploy:
  managed: false
  registry: {{ source: agent-registry, id: "{LOAN_ID}" }}
callers:
  cx-mortgage-orchestrator: {{}}
"""


def _adopt(root: Path, body: str = ADOPTION) -> None:
    _write(root, "teams/ai-coe/agents/loan-pricing.yaml", body)


def _compiled(root: Path, env: str = "dev") -> dict[str, Any]:
    return json.loads(catalog.render_resolved_file(catalog.resolve(root, env)))


def _problems(root: Path, env: str = "dev") -> str:
    with pytest.raises(CatalogError) as exc:
        catalog.resolve(root, env)
    return "\n".join(exc.value.problems)


# ------------------------------------------------------------------------------------------
# derived principal and URLs: pure functions of the runtime resource
# ------------------------------------------------------------------------------------------


def test_runtime_principal_is_a_pure_function_of_the_engine_resource():
    assert runtime_principal("agent_runtime", RESOURCE) == (
        "principal://{trust_domain:ai_coe_agents}/resources/aiplatform/projects/{project_number:ai_coe_agents}"
        f"/locations/{{region}}/reasoningEngines/{ENGINE}")
    other = RESOURCE.replace("{region}", "europe-west9")
    assert runtime_principal("agent_runtime", other).endswith(f"/locations/europe-west9/reasoningEngines/{ENGINE}")
    assert runtime_principal("agent_runtime", RESOURCE) == runtime_principal("agent_runtime", RESOURCE)


@pytest.mark.parametrize(("runtime", "resource", "message"), [
    ("cloud_run", RESOURCE, "runtime 'cloud_run' is not supported for adoption yet"),
    ("gke", RESOURCE, "runtime 'gke' is not supported for adoption yet"),
    (None, RESOURCE, "runtime 'None' is not supported"),
    ("agent_runtime", RESOURCE.replace("{project_number:ai_coe_agents}", "1" * 12), "of a layer-1 project"),
    ("agent_runtime", "//run.googleapis.com/projects/{project:x}/locations/{region}/services/s", "not an Agent Runtime"),
    ("agent_runtime", RESOURCE + "/sessions", "not an Agent Runtime engine"),
])
def test_runtime_principal_fails_closed(runtime, resource, message):
    with pytest.raises(CatalogError, match=message):
        runtime_principal(runtime, resource)


def test_engine_urls_cover_the_engine_and_only_the_engine():
    urls = engine_urls(RESOURCE)
    base = f"projects/{{project_number:ai_coe_agents}}/locations/{{region}}/reasoningEngines/{ENGINE}"
    assert urls == [
        f"https://{{region}}-aiplatform.googleapis.com/{version}/{base}{suffix}"
        for version in ("v1", "v1beta1") for suffix in ("/", ":query", ":streamQuery")
    ]
    assert QUERY_URL in urls
    # Never a host-level URL (that is the Google API entry every agent runtime may reach).
    assert all(f"/reasoningEngines/{ENGINE}" in u for u in urls)


# ------------------------------------------------------------------------------------------
# adoption compile
# ------------------------------------------------------------------------------------------


def test_adopted_agent_runtime_entry_compiles_to_a_governance_entry_with_exactly_its_callers(repo):
    baseline = _compiled(repo)
    _adopt(repo)
    resolved = catalog.resolve(repo, "dev")
    assert resolved.adopted == {"loan-pricing": "agent-registry"}
    target = resolved.targets["loan-pricing"]
    assert target["runtime"] == "agent_runtime" and target["runtime_resource"] == RESOURCE
    assert target["deploy"]["endpoint"] == QUERY_URL
    assert target["description"] == "Loan pricing agent deployed by another team (synthetic)."

    data = json.loads(catalog.render_resolved_file(resolved))
    assert data["adopted_targets"] == {"loan-pricing": {
        "registry": "agent",
        "registry_id": LOAN_ID,
        "source": "agent-registry",
        "runtime": "agent_runtime",
        "runtime_resource": RESOURCE,
        "project": "ai_coe_agents",
        "location": "{region}",
        "engine": ENGINE,
        "urls": engine_urls(RESOURCE),
        "protocol_binding": "HTTP_JSON",
        "description": "Loan pricing agent deployed by another team (synthetic). Adopted from agent-registry entry "
                       f"{LOAN_ID}. Used by: cx-mortgage-orchestrator.",
        "owner_team": "ai-coe",
        "principal": runtime_principal("agent_runtime", RESOURCE),
    }}
    # The grant: exactly the listed caller, on the adopted entry, and nowhere else.
    assert data["iap_egress"]["agents"]["loan-pricing"] == [ORCHESTRATOR]
    expected = json.loads(json.dumps(baseline["iap_egress"]))
    expected["agents"]["loan-pricing"] = [ORCHESTRATOR]
    assert data["iap_egress"] == expected
    for block in UNCHANGED:
        assert data[block] == baseline[block], f"{block} changed by an adoption that isn't a caller"
    assert "loan-pricing" not in data["external_targets"], "adoption is never a new external_targets entry"
    assert data["inventory_summary"] == {"agent-registry": {"adopted": 1, "discovered": 1}}


def test_adopted_target_with_no_callers_gets_an_empty_policy(repo):
    _adopt(repo, ADOPTION.split("callers:")[0])
    data = _compiled(repo)
    assert data["iap_egress"]["agents"]["loan-pricing"] == []
    assert data["adopted_targets"]["loan-pricing"]["description"].endswith("Used by: no caller declared.")


def test_discovered_entries_alone_compile_byte_identically(repo):
    """I3: any number of discovered entries leaves every compiled block byte-identical."""
    data = _compiled(repo)
    files_only = json.loads((REPO / "catalog/.resolved/dev.json").read_text())
    for block in (*UNCHANGED, "iap_egress"):
        assert data[block] == files_only[block], block
    assert "adopted_targets" not in data


@pytest.mark.parametrize("env", ["dev", "prd"])
def test_files_only_catalog_has_no_adopted_targets_block(env):
    assert "adopted_targets" not in json.loads((REPO / "catalog/.resolved" / f"{env}.json").read_text())


def test_adoption_needs_the_pin_and_the_pinned_id(repo):
    """I4: a name match without a pin is an error; a pinned id missing from the snapshot fails closed."""
    _write(repo, "teams/ai-coe/agents/loan-helper.yaml", ADOPTION.replace("name: loan-pricing", "name: loan-helper")
           .replace(f'  registry: {{ source: agent-registry, id: "{LOAN_ID}" }}\n', "  endpoint: https://x.example.com\n"))
    assert "inventory.loan-helper has the name of catalog target 'loan-helper'" in _problems(repo)

    (repo / "catalog/teams/ai-coe/agents/loan-helper.yaml").unlink()
    _adopt(repo, ADOPTION.replace(f"reasoningEngines:{ENGINE}", "reasoningEngines:999"))
    assert "which it doesn't report: re-fetch agent-registry or remove the pin" in _problems(repo)


def test_set_file_fields_must_equal_the_registry_and_unset_ones_are_filled(repo):
    same = ADOPTION + f"runtime: agent_runtime\nruntime_resource: \"{RESOURCE}\"\n"
    _adopt(repo, same.replace("  managed: false\n", f"  managed: false\n  endpoint: \"{QUERY_URL}\"\n"))
    resolved = catalog.resolve(repo, "dev")
    assert resolved.targets["loan-pricing"]["runtime_resource"] == RESOURCE
    assert resolved.provenance["targets.loan-pricing.description"]["source"] == "agent-registry"

    _adopt(repo, ADOPTION + f"runtime_resource: \"{RESOURCE.replace(ENGINE, '7770007')}\"\n")
    problems = _problems(repo)
    assert "targets.loan-pricing.runtime_resource: agent-registry" in problems and "strict field" in problems
    _adopt(repo, ADOPTION.replace("  managed: false\n", "  managed: false\n  endpoint: https://other.example.com\n"))
    assert "targets.loan-pricing.deploy.endpoint: agent-registry" in _problems(repo)
    _adopt(repo, ADOPTION + "runtime: gke\n")
    assert "targets.loan-pricing.runtime: agent-registry" in _problems(repo)


def test_cloud_run_and_gke_adoption_is_refused(repo):
    """Nothing is granted silently for a runtime adoption doesn't support yet."""
    _adopt(repo, ADOPTION.replace(LOAN_ID, QUOTE_ID))
    problems = _problems(repo)
    assert "which reports runtime 'cloud_run': only agent_runtime entries are supported for adoption yet" in problems

    snapshot = _entries()
    snapshot[1]["inventory"]["runtime"] = "gke"
    snapshot[1]["inventory"]["runtime_resource"] = "//container.googleapis.com/projects/{project:x}/zones/z/clusters/c"
    (repo / SNAPSHOT).write_text(_snapshot(snapshot))
    assert "reports runtime 'gke': only agent_runtime" in _problems(repo)

    # A pinned entry without a runtime (a manual registration) stays an external target, as before.
    del snapshot[1]["inventory"]["runtime"], snapshot[1]["inventory"]["runtime_resource"]
    (repo / SNAPSHOT).write_text(_snapshot(snapshot))
    data = _compiled(repo)
    assert "adopted_targets" not in data
    assert data["external_targets"]["loan-pricing"]["url"] == "https://quote-bot.{domain}"
    assert data["iap_egress"]["agents"]["loan-pricing"] == [ORCHESTRATOR]

    # An example adoption compiles nothing, so it may pin any entry (inventory metadata only).
    _adopt(repo, ADOPTION.replace(LOAN_ID, QUOTE_ID).replace("owner_team: ai-coe\n", "owner_team: ai-coe\nexample: true\n"))
    assert "adopted_targets" not in _compiled(repo)


def test_adopted_runtime_entry_must_be_an_agent_engine(repo):
    snapshot = _entries()
    snapshot[0]["inventory"]["runtime_resource"] = RESOURCE.replace("{project_number:ai_coe_agents}", "{project:x}")
    (repo / SNAPSHOT).write_text(_snapshot(snapshot))
    _adopt(repo)
    assert "is not an Agent Runtime engine of a layer-1 project" in _problems(repo)


# ------------------------------------------------------------------------------------------
# adopted agents as callers: derived principal, attribution (I10)
# ------------------------------------------------------------------------------------------

PRICING_API = """\
name: pricing-api
kind: http
owner_team: ai-coe
description: Pricing API the adopted agent calls (synthetic).
deploy: { managed: false, endpoint: "https://pricing.{domain}" }
callers:
  loan-pricing: {}
"""


def test_adopted_agent_that_calls_gets_its_derived_principal_and_a_team_row(repo):
    baseline = _compiled(repo)
    _adopt(repo)
    _write(repo, "external/legacy/pricing-api.yaml", PRICING_API)
    data = _compiled(repo)
    assert data["iap_egress"]["endpoints"]["pricing-api"] == [ADOPTED]
    rows = [r for r in data["attribution"]["principal_teams"] if r["caller"] == "loan-pricing"]
    assert rows == [{"principal": ADOPTED, "principal_type": "agent_runtime", "caller": "loan-pricing",
                     "team": "ai-coe", "cost_centers": rows[0]["cost_centers"]}]
    assert rows[0]["cost_centers"] == catalog.resolve(repo, "dev").teams["ai-coe"]["cost_centers"]
    assert data["adopted_targets"]["loan-pricing"]["principal"].startswith("principal://{trust_domain:ai_coe_agents}/")
    # No IAM grant, no allowlist: the adopted agent only appears where the gateway enforces it.
    assert data["catalog"] == baseline["catalog"]
    assert data["attribution"]["allowlists"] == baseline["attribution"]["allowlists"]


def test_allowed_implies_attributable_with_adopted_callers(repo, monkeypatch):
    """I10: the compile check covers derived principals (a missing row fails the compile)."""
    _adopt(repo)
    _write(repo, "external/legacy/pricing-api.yaml", PRICING_API)
    resolved = catalog.resolve(repo, "dev")
    catalog.compile_attribution(resolved)  # passes: every gateway member has a row

    from esmeralda.catalog import resolver

    real = resolver.compile_principal_teams
    monkeypatch.setattr(resolver, "compile_principal_teams",
                        lambda r: [row for row in real(r) if row["principal"] != ADOPTED])
    with pytest.raises(CatalogError, match=f"iap_egress.endpoints.pricing-api: '{ADOPTED}' is allowed"):
        catalog.compile_attribution(resolved)


def test_adopted_agent_may_not_call_a_deployed_target(repo):
    _adopt(repo)
    path = repo / "catalog/teams/ai-coe/mcp/legacy-dms.yaml"
    text = path.read_text()
    assert "callers:\n" in text
    path.write_text(text.replace("callers:\n", "callers:\n  loan-pricing: {}\n", 1))
    assert "caller 'loan-pricing' is an adopted agent; it may call external and adopted targets" in _problems(repo)


# ------------------------------------------------------------------------------------------
# I1: no grant from outside Git; I7: determinism
# ------------------------------------------------------------------------------------------


def test_registry_contributes_inventory_fields_only(repo):
    _adopt(repo)
    _write(repo, "external/legacy/pricing-api.yaml", PRICING_API)
    resolved = catalog.resolve(repo, "dev")
    external = {key: rec for key, rec in resolved.provenance.items() if rec["source"] == "agent-registry"}
    allowed = ("deploy.endpoint", "runtime", "runtime_resource", "description", "registry")
    for key in external:
        section, name, *path = key.split(".", 2)
        if section == "inventory":
            continue
        assert section == "targets" and name == "loan-pricing" and path, key
        assert any(path[0] == a or path[0].startswith(a + ".") for a in allowed), key
    # The callers (the grant) come from the catalog file.
    assert resolved.provenance["targets.loan-pricing"]["source"] == "catalog-files"
    assert not any(k.startswith("targets.loan-pricing.callers") for k in external)


def test_adoption_resolves_to_identical_bytes(repo):
    """I7: a re-run and different hash seeds give identical bytes (snapshot order is fixed: sorted by id)."""
    _adopt(repo)
    _write(repo, "external/legacy/pricing-api.yaml", PRICING_API)
    first = catalog.render_resolved_file(catalog.resolve(repo, "dev"))
    assert catalog.render_resolved_file(catalog.resolve(repo, "dev")) == first
    script = "import sys; from pathlib import Path; from esmeralda import catalog; " \
             "sys.stdout.write(catalog.render_resolved_file(catalog.resolve(Path(sys.argv[1]), 'dev')))"
    for seed in ("0", "1", "4242"):
        out = subprocess.run([sys.executable, "-c", script, str(repo)], capture_output=True, text=True, check=True,
                             env={**os.environ, "PYTHONHASHSEED": seed})
        assert out.stdout == first, seed


def test_show_marks_the_adopted_grant_enforced(repo):
    _adopt(repo)
    rows = catalog.show(catalog.resolve(repo, "dev"), target="loan-pricing")["targets"]
    assert rows[0]["adopted_from"] == "agent-registry"
    assert rows[0]["registry"].startswith("agents (adopted:")
    assert [(c["caller"], c["enforced"]) for c in rows[0]["callers"]] == [("cx-mortgage-orchestrator", True)]
