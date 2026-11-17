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

"""The ``agent_registry`` catalog source: fetch, exclusion of what Esmeralda registers, discovered
inventory, adoption by pin only, drift.

Everything is synthetic: recorded ``gcloud alpha agent-registry ... list`` responses (shaped like
the real ones) and unit outputs are served by fakes (``esmeralda.catalog.adapters._live``), so no
test touches the network. Values that look like environment literals (12-digit numbers, an
organization id) are assembled at run time so that this file never contains one.
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
from esmeralda.catalog.adapters.agent_registry import AgentRegistrySource, derive_name
from esmeralda.catalog.snapshot import snapshot_leaks
from esmeralda.ops import guard

REPO = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "catalog_sources"
SNAPSHOT = "catalog/snapshots/agent-registry/dev.json"
LABELS_GOLDEN = FIXTURES / "catalog" / "snapshots" / "project-labels" / "dev.json"
COMPILED = ("catalog", "external_targets", "google_apis", "iap_egress", "attribution")

REGION = "europe-west9"
DOMAIN = "example.internal"
ORG = "9876" + "54321"
IDS = {
    "governance": "demo-governance-c3d4",
    "ai_coe_agents": "demo-ai-coe-agents-c3d4",
    "cx_agents": "demo-cx-agents-c3d4",
    "mcps": "demo-mcps-c3d4",
    "gateway": "demo-gateway-c3d4",
}
NUMBERS = {alias: str(200000000000 + i * 7777) for i, alias in enumerate(sorted(IDS), start=1)}
ENGINES = {"ai-coe-mortgage-specialist": "4440001", "cx-mortgage-orchestrator": "4440002"}
WORKSPACE_AGENT = "urn:agent:googleapis.com:locations:global:workspaceagent:workspaceagent--a2a"


def _num(alias: str) -> str:
    return NUMBERS[alias]


def _service_ref(sid: str) -> dict[str, Any]:
    uri = f"//agentregistry.googleapis.com/projects/{_num('governance')}/locations/{REGION}/services/{sid}"
    return {"agentregistry.googleapis.com/system/RuntimeReference": {"uri": uri}}


def _urn(kind: str, alias: str, tail: str) -> str:
    return f"urn:{kind}:projects-{_num(alias)}:projects:{_num(alias)}:locations:{REGION}:{tail}"


def _engine_agent(alias: str, engine: str, display: str, **extra: Any) -> dict[str, Any]:
    resource = f"//aiplatform.googleapis.com/projects/{_num(alias)}/locations/{REGION}/reasoningEngines/{engine}"
    return {
        "agentId": _urn("agent", alias, f"aiplatform:reasoningEngines:{engine}"),
        "attributes": {
            "agentregistry.googleapis.com/system/Framework": {"framework": "google-adk"},
            "agentregistry.googleapis.com/system/RuntimeIdentity": {
                "principal": f"principal://agents.global.org-{ORG}.system.id.goog/resources/aiplatform/projects/"
                             f"{_num(alias)}/locations/{REGION}/reasoningEngines/{engine}"},
            "agentregistry.googleapis.com/system/RuntimeReference": {"uri": resource},
        },
        "createTime": "2026-10-07T19:00:36.789659Z",
        "displayName": display,
        "location": REGION,
        "name": f"projects/{IDS[alias]}/locations/{REGION}/agents/agentregistry-0000-{engine}",
        "updateTime": "2026-10-07T19:00:36.789659Z",
        **extra,
    }


def _workspace_agent(alias: str) -> dict[str, Any]:
    return {
        "agentId": WORKSPACE_AGENT, "displayName": "Workspace Agent", "location": "global",
        "name": f"projects/{IDS[alias]}/locations/{REGION}/agents/agentregistry-0000-workspace",
        "protocols": [{"interfaces": [{"protocolBinding": "HTTP_JSON", "url": "https://workspaceagent.googleapis.com/a2a"}],
                       "type": "A2A_AGENT"}],
    }


def _service_entry(kind: str, id_field: str, sid: str, url: str, binding: str, description: str) -> dict[str, Any]:
    return {
        "attributes": _service_ref(sid),
        "createTime": "2026-10-07T18:36:16.906497Z",
        "description": description,
        "displayName": sid,
        id_field: _urn(kind, "governance", f"agentregistry:services:{sid}"),
        "interfaces": [{"protocolBinding": binding, "url": url}],
        "name": f"projects/{IDS['governance']}/locations/{REGION}/{kind}s/agentregistry-0000-{sid}",
        "updateTime": "2026-10-07T18:36:16.906497Z",
    }


def _registry() -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Recorded list responses per (project alias, collection) (synthetic, shaped like the API)."""
    loan_helper = _engine_agent(
        "ai_coe_agents", "5550001", "Loan Helper",
        description="Discovered loan helper agent (synthetic).",
        card={"content": {"name": "loan-helper", "description": "Card description",
                          "skills": [{"id": "quote", "name": "Quote"}, {"id": "amortize", "name": "Amortize"}],
                          "supportedInterfaces": [{"protocolBinding": "HTTP+JSON",
                                                   "url": f"https://loan-helper.{DOMAIN}/a2a"}]}},
    )
    quote_bot = {
        "agentId": _urn("agent", "ai_coe_agents", "run:services:quote-bot"),
        "attributes": {"agentregistry.googleapis.com/system/RuntimeReference": {
            "uri": f"//run.googleapis.com/projects/{IDS['ai_coe_agents']}/locations/{REGION}/services/quote-bot"}},
        "description": "Quote bot on Cloud Run (synthetic).",
        "displayName": "quote-bot",
        "protocols": [{"interfaces": [{"protocolBinding": "JSONRPC", "url": "https://quote-bot-abc123-ew.a.run.app"}],
                       "type": "A2A_AGENT"}],
        "skills": [{"id": "quote", "name": "Quote"}],
        "uid": "volatile-uid",
    }
    return {
        ("governance", "agents"): [
            _workspace_agent("governance"),
            _service_entry("agent", "agentId", "ai-coe-mortgage-specialist",
                           f"https://ai-coe-mortgage-specialist.{DOMAIN}", "HTTP+JSON", "Specialist. Used by: x."),
        ],
        ("governance", "mcp-servers"): [
            _service_entry("mcpServer", "mcpServerId", "legacy-dms", f"https://legacy-dms.{DOMAIN}/mcp", "JSONRPC",
                           "Legacy DMS. Used by: x."),
            _service_entry("mcpServer", "mcpServerId", "pricing-tools", f"https://pricing-tools.{DOMAIN}/mcp",
                           "JSONRPC", "Pricing tools registered by another team (synthetic)."),
        ],
        ("governance", "endpoints"): [
            _service_entry("endpoint", "endpointId", "aiplatform", "https://aiplatform.googleapis.com", "HTTP_JSON",
                           "Vertex AI."),
            _service_entry("endpoint", "endpointId", f"{REGION}-aiplatform",
                           f"https://{REGION}-aiplatform.googleapis.com", "HTTP_JSON", "Vertex AI regional."),
            _service_entry("endpoint", "endpointId", "external-probe", f"https://external-probe.{DOMAIN}",
                           "HTTP_JSON", "Probe."),
            _service_entry("endpoint", "endpointId", "llm-gateway", f"https://apigee.{DOMAIN}/llm", "HTTP_JSON",
                           "LLM gateway."),
        ],
        ("ai_coe_agents", "agents"): [
            _workspace_agent("ai_coe_agents"),
            _engine_agent("ai_coe_agents", ENGINES["ai-coe-mortgage-specialist"], "ai-coe-mortgage-specialist-dev"),
            loan_helper,
            quote_bot,
        ],
        ("ai_coe_agents", "endpoints"): [
            _service_entry("endpoint", "endpointId", "core-gapi-services", "https://telemetry.googleapis.com",
                           "JSONRPC", "Core Google APIs for the runtime."),
        ],
        ("cx_agents", "agents"): [
            _engine_agent("cx_agents", ENGINES["cx-mortgage-orchestrator"], "cx-mortgage-orchestrator-dev"),
        ],
        ("mcps", "agents"): [_workspace_agent("mcps")],
    }


@pytest.fixture
def live(monkeypatch) -> dict[str, Any]:
    """Fake unit outputs and gcloud; mutate the returned state to change what is live."""
    state: dict[str, Any] = {"registry": _registry(), "calls": [], "unreachable": False}
    by_id = {pid: alias for alias, pid in IDS.items()}
    outputs = {
        "layer-1-projects": {f"{alias}_project_id": pid for alias, pid in IDS.items()},
        "layer-2-networking": {"entrypoint_domain": DOMAIN},
        **{f"layer-5-workloads/agents/{name}": {
            "engine_id": f"projects/{_num('ai_coe_agents')}/locations/{REGION}/reasoningEngines/{engine}"}
           for name, engine in ENGINES.items()},
    }
    state["outputs"] = outputs

    def unit_outputs(_root: Path, env: str, unit: str) -> dict[str, Any]:
        assert env == "dev"
        return copy.deepcopy(state["outputs"].get(unit, {}))

    def gcloud_json(_root: Path, args: list[str], what: str) -> Any:
        state["calls"].append(args)
        if state["unreachable"]:
            raise SourceUnreachable(f"{what} failed: synthetic outage")
        if args[:2] == ["projects", "describe"]:
            alias = by_id[args[2]]
            return {"projectId": args[2], "projectNumber": _num(alias), "parent": {"id": ORG, "type": "organization"}}
        assert args[:2] == ["alpha", "agent-registry"] and args[3] == "list", "read-only calls only"
        project = by_id[args[4].split("=", 1)[1]]
        assert args[5] == f"--location={REGION}"
        return copy.deepcopy(state["registry"].get((project, args[2]), []))

    monkeypatch.setattr(_live, "unit_outputs", unit_outputs)
    monkeypatch.setattr(_live, "env_region", lambda _root, _env: REGION)
    monkeypatch.setattr(_live, "gcloud_json", gcloud_json)
    return state


def _enable(root: Path, name: str, extra: str = "") -> None:
    path = root / "catalog/sources.yaml"
    text = path.read_text()
    start = text.index(f"  - name: {name}\n")
    disabled = text.index("    enabled: false\n", start)
    path.write_text(text[:disabled] + extra + text[disabled + len("    enabled: false\n"):])


@pytest.fixture
def repo(tmp_path: Path, live) -> Path:
    """The repository catalog with agent-registry enabled and its snapshot fetched from the fakes."""
    root = tmp_path / "repo"
    shutil.copytree(REPO / "catalog", root / "catalog")
    (root / "contracts").mkdir()
    for name in ("catalog.schema.json", "catalog-snapshot.schema.json"):
        shutil.copy(REPO / "contracts" / name, root / "contracts")
    (root / "infrastructure/live").mkdir(parents=True)
    (root / "infrastructure/live/root.hcl").write_text("")
    _enable(root, "agent-registry")
    commands.fetch_sources(root, "dev", "agent-registry")
    return root


def _write(root: Path, rel: str, body: str) -> None:
    path = root / "catalog" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("schema: esmeralda.catalog\nschema_version: 1\n" + body)


def _problems(root: Path, env: str = "dev") -> str:
    with pytest.raises(CatalogError) as exc:
        catalog.resolve(root, env)
    return "\n".join(exc.value.problems)


def _entries(root: Path) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in json.loads((root / SNAPSHOT).read_text())["entries"]}


LOAN_ID = "urn:agent:projects-{project_number:ai_coe_agents}:projects:{project_number:ai_coe_agents}:locations:" \
          "{region}:aiplatform:reasoningEngines:5550001"
PRICING_ID = "urn:mcpServer:projects-{project_number:governance}:projects:{project_number:governance}:locations:" \
             "{region}:agentregistry:services:pricing-tools"


# ------------------------------------------------------------------------------------------
# fetch
# ------------------------------------------------------------------------------------------


def test_fetch_keeps_only_what_esmeralda_did_not_register(repo, live):
    entries = _entries(repo)
    assert sorted(entries) == ["loan-helper", "pricing-tools", "quote-bot"]
    assert entries["loan-helper"] == {
        "id": LOAN_ID,
        "name": "loan-helper",
        "inventory": {
            "kind": "a2a",
            "description": "Discovered loan helper agent (synthetic).",
            "deploy": {"endpoint": "https://loan-helper.{domain}/a2a"},
            "runtime": "agent_runtime",
            "runtime_resource": "//aiplatform.googleapis.com/projects/{project_number:ai_coe_agents}/locations/"
                                "{region}/reasoningEngines/5550001",
            "registry": {"id": LOAN_ID, "skills": ["amortize", "quote"],
                         "interfaces": [{"url": "https://loan-helper.{domain}/a2a", "protocol_binding": "HTTP+JSON"}]},
        },
    }
    assert entries["pricing-tools"]["inventory"]["kind"] == "mcp" and entries["pricing-tools"]["id"] == PRICING_ID
    assert "runtime" not in entries["pricing-tools"]["inventory"], "a manual registration has no runtime"
    assert entries["quote-bot"]["inventory"]["runtime"] == "cloud_run"
    assert entries["quote-bot"]["inventory"]["runtime_resource"].startswith(
        "//run.googleapis.com/projects/{project:ai_coe_agents}/locations/{region}/")


def test_fetched_snapshot_is_sanitized_and_passes_the_guard(repo, live):
    text = (repo / SNAPSHOT).read_text()
    for literal in [*IDS.values(), *NUMBERS.values(), ORG, DOMAIN, REGION, "principal://", "RuntimeIdentity",
                    "createTime", "volatile-uid", "workspaceagent", "Used by"]:
        assert literal not in text
    assert snapshot_leaks(text) == []
    assert guard.scan_files(repo, [SNAPSHOT]) == []
    verbs = {tuple(args[:2]) if args[0] == "projects" else (args[0], args[1], args[3]) for args in live["calls"]}
    assert verbs == {("projects", "describe"), ("alpha", "agent-registry", "list")}


def test_fetch_is_idempotent_and_independent_of_response_order(repo, live):
    before = (repo / SNAPSHOT).read_text()
    rng = random.Random(7)
    for items in live["registry"].values():
        rng.shuffle(items)
    result = commands.fetch_sources(repo, "dev", "agent-registry")["sources"][0]
    assert result["status"] == "unchanged" and (repo / SNAPSHOT).read_text() == before


def test_derived_name_collisions_fail_and_options_names_resolve_them(repo, live):
    clone = copy.deepcopy(live["registry"][("ai_coe_agents", "agents")][2])
    clone["agentId"] = clone["agentId"].replace("5550001", "5550009")
    clone["description"] = "Another loan helper."
    live["registry"][("cx_agents", "agents")].append(clone)
    with pytest.raises(CatalogError, match="two entries have the name 'loan-helper'"):
        commands.fetch_sources(repo, "dev", "agent-registry", dry_run=True)

    other = LOAN_ID.replace("5550001", "5550009")
    _enable_options(repo, "      names: { " + json.dumps(other) + ": loan-helper-two }\n")
    assert commands.fetch_sources(repo, "dev", "agent-registry", dry_run=True)["sources"][0]["added"] == [other]


def _enable_options(root: Path, extra: str) -> None:
    """Add option lines to the agent-registry entry of sources.yaml."""
    path = root / "catalog/sources.yaml"
    assert "      # names:" in path.read_text()
    path.write_text(path.read_text().replace("      # names:", extra + "      # names:"))


def test_an_entry_without_a_valid_name_fails_the_fetch(repo, live):
    live["registry"][("ai_coe_agents", "agents")][2]["displayName"] = "42"
    with pytest.raises(CatalogError, match="gives no valid catalog name .* map it in options.names"):
        commands.fetch_sources(repo, "dev", "agent-registry", dry_run=True)


def test_fetch_fails_closed(repo, live, capsys):
    before = (repo / SNAPSHOT).read_text()
    live["unreachable"] = True
    assert cli.main(["catalog", "fetch", "--source", "agent-registry", "--env", "dev", "--repo", str(repo)]) == 2
    assert "synthetic outage" in capsys.readouterr().err
    assert (repo / SNAPSHOT).read_text() == before

    live["unreachable"] = False
    (repo / "catalog/.resolved/dev.json").unlink()
    with pytest.raises(CatalogError, match="to exclude what Esmeralda registers"):
        commands.fetch_sources(repo, "dev", "agent-registry", dry_run=True)


def test_unknown_project_alias_is_reported(repo, live):
    path = repo / "catalog/sources.yaml"
    path.write_text(path.read_text().replace("[governance, ai_coe_agents, cx_agents, mcps]", "[governance, nope]"))
    with pytest.raises(SourceUnreachable, match="no nope_project_id output"):
        commands.fetch_sources(repo, "dev", "agent-registry", dry_run=True)


def test_derive_name():
    assert derive_name("Loan Helper") == "loan-helper"
    assert derive_name("  pricing_tools--v2 ") == "pricing-tools-v2"


# ------------------------------------------------------------------------------------------
# resolve: discovered inventory, never compiled
# ------------------------------------------------------------------------------------------


def test_discovered_entries_are_inventory_and_never_compiled(repo):
    resolved = catalog.resolve(repo, "dev")
    assert sorted(resolved.inventory) == ["loan-helper", "pricing-tools", "quote-bot"]
    assert resolved.adopted == {}
    assert resolved.provenance["inventory.loan-helper"] == {
        "source": "agent-registry", "origin": f"snapshots/agent-registry/dev.json#{LOAN_ID}"}
    data = json.loads(catalog.render_resolved_file(resolved))
    files_only = json.loads((REPO / "catalog/.resolved/dev.json").read_text())
    for block in COMPILED:
        assert data[block] == files_only[block], f"{block} changed by discovered inventory"
    assert data["inventory_summary"] == {"agent-registry": {"adopted": 0, "discovered": 3}}
    assert "inventory" not in data


def test_resolve_is_deterministic(repo):
    first = catalog.render_resolved_file(catalog.resolve(repo, "dev"))
    assert catalog.render_resolved_file(catalog.resolve(repo, "dev")) == first


def test_adoption_only_by_explicit_pin(repo):
    _write(repo, "teams/ai-coe/mcp/pricing-tools-adopted.yaml", f"""\
name: pricing
kind: mcp
owner_team: ai-coe
example: true
deploy:
  managed: false
  endpoint: "https://pricing-tools.{{domain}}/mcp"
  registry: {{ source: agent-registry, id: "{PRICING_ID}" }}
""")
    resolved = catalog.resolve(repo, "dev")
    assert resolved.adopted == {"pricing": "agent-registry"}
    assert "pricing-tools" not in resolved.inventory
    assert resolved.targets["pricing"]["description"] == "Pricing tools registered by another team (synthetic)."
    assert resolved.provenance["targets.pricing.description"]["source"] == "agent-registry"
    assert "callers" not in resolved.targets["pricing"], "adoption brings no caller"

    path = repo / "catalog/teams/ai-coe/mcp/pricing-tools-adopted.yaml"
    path.write_text(path.read_text().replace("services:pricing-tools", "services:gone"))
    assert "which it doesn't report" in _problems(repo)


def test_a_name_match_without_a_pin_is_an_error(repo):
    _write(repo, "teams/ai-coe/agents/quote-bot.yaml", """\
name: quote-bot
kind: a2a
owner_team: ai-coe
example: true
deploy: { managed: false, endpoint: "https://quote-bot.example.com" }
""")
    assert "inventory.quote-bot has the name of catalog target 'quote-bot'" in _problems(repo)


@pytest.mark.parametrize(("value", "expected"), [
    ({"kind": "a2a", "callers": {"test-vm": {}}}, "may not set inventory.rogue.callers"),
    ({"kind": "a2a", "identity": {"type": "group", "id": "g"}}, "may not set inventory.rogue.identity"),
    ({"kind": "a2a", "google_apis": ["storage"]}, "may not set inventory.rogue.google_apis"),
])
def test_a_registry_adapter_can_never_set_authorization_data(repo, monkeypatch, value, expected):
    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))

    @register_source("agent_registry")
    class Rogue(AgentRegistrySource):
        def complete(self, layer: Layer, merged: Layer) -> Layer:
            layer.inventory["rogue"] = value
            return layer

    assert expected in _problems(repo)


def test_project_labels_fill_the_owner_of_discovered_agents(repo):
    _enable(repo, "project-labels", "")
    path = repo / "catalog/sources.yaml"
    path.write_text(path.read_text().replace("      # team_map: { appdev-tools: platform }",
                                             "      team_map: { appdev-tools: platform }")
                    .replace("      # cost_center_map: {}",
                             "      cost_center_map: { central-developer-tools: cc-platform }"))
    shutil.copytree(LABELS_GOLDEN.parent, repo / "catalog/snapshots/project-labels")
    resolved = catalog.resolve(repo, "dev")
    assert resolved.inventory["loan-helper"]["owner_team"] == "ai-coe"  # {project_number:ai_coe_agents}
    assert resolved.inventory["quote-bot"]["owner_team"] == "ai-coe"  # {project:ai_coe_agents}
    assert "owner_team" not in resolved.inventory["pricing-tools"], "no runtime project, no owner"
    assert resolved.provenance["inventory.loan-helper.owner_team"]["source"] == "project-labels"


# ------------------------------------------------------------------------------------------
# show, diff, offline
# ------------------------------------------------------------------------------------------


def test_cli_show_inventory_lists_discovered_entries(repo, capsys):
    assert cli.main(["catalog", "show", "--env", "dev", "--inventory", "--json", "--repo", str(repo)]) == 0
    rows = json.loads(capsys.readouterr().out)["inventory"]
    assert [(r["name"], r["kind"], r["source"], r["compiled"]) for r in rows] == [
        ("loan-helper", "a2a", "agent-registry", False),
        ("pricing-tools", "mcp", "agent-registry", False),
        ("quote-bot", "a2a", "agent-registry", False),
    ]
    assert cli.main(["catalog", "show", "--env", "dev", "--inventory", "--repo", str(repo)]) == 0
    assert "loan-helper (a2a) id=" + LOAN_ID + " from agent-registry runtime=agent_runtime" in capsys.readouterr().out


def test_cli_diff_reports_discovered_and_live_missing_entries(repo, live, capsys):
    args = ["catalog", "diff", "--env", "dev", "--json", "--repo", str(repo)]
    assert cli.main(args) == 3
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert {(f["kind"], f["subject"]) for f in findings} == {
        ("discovered", "inventory.loan-helper"), ("discovered", "inventory.pricing-tools"),
        ("discovered", "inventory.quote-bot")}

    live["registry"][("governance", "mcp-servers")] = [
        e for e in live["registry"][("governance", "mcp-servers")] if "legacy-dms" not in e["mcpServerId"]]
    assert cli.main([*args, "--live"]) == 3
    findings = json.loads(capsys.readouterr().out)["findings"]
    missing = {f["subject"] for f in findings if f["kind"] == "missing"}
    assert "service legacy-dms" in missing and "service external-probe" not in missing
    assert all(f["source"] == "agent-registry" for f in findings if f["kind"] == "missing")


def test_resolve_show_and_diff_open_no_socket(repo, monkeypatch):
    def no_network(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the catalog opened a network socket")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    args = ["--env", "dev", "--repo", str(repo)]
    assert cli.main(["catalog", "resolve", "--write", *args]) == 0
    assert cli.main(["catalog", "resolve", "--check", *args]) == 0
    assert cli.main(["catalog", "show", "--inventory", *args]) == 0
    assert cli.main(["catalog", "diff", *args]) == 3


def test_shipped_source_is_disabled():
    rows = {r["name"]: r for r in commands.describe_sources(REPO, "dev")["sources"]}
    assert rows["agent-registry"]["enabled"] is False and rows["agent-registry"]["type"] == "agent_registry"
    assert rows["agent-registry"]["capabilities"]["inventory"] == ["*"]
