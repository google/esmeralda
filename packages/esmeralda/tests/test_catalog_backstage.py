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

"""The ``backstage`` catalog source: fetch (JSON export or the Catalog API), mapping, token
handling, discovered inventory, team profiles, adoption by ``backstage`` pin only, suggestions.

Everything is synthetic: the Backstage entities under ``tests/fixtures/catalog_sources/backstage/``
and, for the Catalog API, a fake Backstage served in-process on 127.0.0.1 (no other network).
Token-like values are assembled at run time so that this file never contains one.
"""

from __future__ import annotations

import copy
import http.server
import json
import random
import shutil
import socket
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from esmeralda import catalog, cli
from esmeralda.catalog import CatalogError, Layer, SnapshotSource, SourceUnreachable, commands, register_source
from esmeralda.catalog import sources as catalog_sources
from esmeralda.catalog.adapters import _live
from esmeralda.catalog.adapters.backstage import BackstageSource, format_ref, parse_ref
from esmeralda.catalog.snapshot import snapshot_leaks
from esmeralda.ops import guard

REPO = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "catalog_sources" / "backstage" / "entities.json"
SNAPSHOT = "catalog/snapshots/backstage/dev.json"
EXPORT = "exports/backstage.json"
COMPILED = ("catalog", "external_targets", "google_apis", "iap_egress", "attribution")
TOKEN = "bs" + "Tok" + "9f3c2a71" + "e4b8d6c0" + "a1b2c3d4" + "e5f6a7b8"  # synthetic, assembled at run time


def _entities() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text())["items"]


def _configure(root: Path, options: str) -> None:
    """Enable the backstage entry of sources.yaml with these option lines."""
    path = root / "catalog/sources.yaml"
    text = path.read_text()
    start = text.index("  - name: backstage\n")
    end = text.index("\n\n", start)
    block = "  - name: backstage\n    type: backstage\n    options:\n" + options
    path.write_text(text[:start] + block.rstrip("\n") + text[end:])


def _write_export(root: Path, entities: list[dict[str, Any]]) -> None:
    path = root / EXPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": entities}))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The repository catalog with backstage enabled from a JSON export, and its snapshot fetched."""
    root = tmp_path / "repo"
    shutil.copytree(REPO / "catalog", root / "catalog")
    (root / "contracts").mkdir()
    for name in ("catalog.schema.json", "catalog-snapshot.schema.json"):
        shutil.copy(REPO / "contracts" / name, root / "contracts")
    (root / "infrastructure/live").mkdir(parents=True)
    (root / "infrastructure/live/root.hcl").write_text("")
    _write_export(root, _entities())
    _configure(root, f"      file: {EXPORT}\n")
    commands.fetch_sources(root, "dev", "backstage")
    return root


def _refetch(root: Path, entities: list[dict[str, Any]]) -> None:
    _write_export(root, entities)
    commands.fetch_sources(root, "dev", "backstage")


def _write(root: Path, rel: str, body: str) -> None:
    path = root / "catalog" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("schema: esmeralda.catalog\nschema_version: 1\n" + body)


def _problems(root: Path, env: str = "dev") -> str:
    with pytest.raises(CatalogError) as exc:
        catalog.resolve(root, env)
    return "\n".join(exc.value.problems)


def _snapshot(root: Path) -> dict[str, Any]:
    return json.loads((root / SNAPSHOT).read_text())


# ------------------------------------------------------------------------------------------
# fetch: mapping and normalization
# ------------------------------------------------------------------------------------------


def test_fetch_maps_entities_to_inventory_and_groups(repo):
    entries = {e["id"]: e for e in _snapshot(repo)["entries"]}
    assert sorted(entries) == ["API:default/pricing-api", "Component:default/loan-helper",
                               "Component:default/pricing-tools", "Group:default/ai-coe", "Group:default/cx-squad",
                               "Resource:default/rates-model"]
    assert entries["Component:default/loan-helper"] == {
        "id": "Component:default/loan-helper",
        "name": "loan-helper",
        "consumes": ["API:default/pricing-api"],
        "inventory": {
            "kind": "a2a",
            "description": "Loan helper agent registered in Backstage (synthetic).",
            "owner_team": "ai-coe",
            "system": "mortgage",
            "deploy": {"endpoint": "https://loan-helper.example.com/a2a"},  # from the API it provides
            "registry": {"id": "Component:default/loan-helper"},
        },
    }
    assert entries["Component:default/pricing-tools"]["inventory"] == {
        "kind": "mcp", "description": "Pricing tools MCP server (synthetic)", "owner_team": "ai-coe",
        "system": "mortgage", "deploy": {"endpoint": "https://pricing-tools.example.com/mcp"},
        "registry": {"id": "Component:default/pricing-tools"}}
    assert entries["API:default/pricing-api"]["inventory"]["kind"] == "http"
    assert entries["API:default/pricing-api"]["inventory"]["owner_team"] == "cx"  # esmeralda.dev/team on the group
    assert entries["Resource:default/rates-model"]["inventory"]["kind"] == "model"
    assert entries["Group:default/ai-coe"] == {
        "id": "Group:default/ai-coe", "team": "ai-coe",
        "profile": {"description": "AI Center of Excellence, as Backstage describes it (synthetic).",
                    "oncall": "ai-coe-pager", "contacts": ["#ai-coe-help"]},  # the email is dropped
    }
    assert entries["Group:default/cx-squad"]["team"] == "cx"


def test_fetched_snapshot_is_sanitized_and_passes_the_guard(repo):
    text = (repo / SNAPSHOT).read_text()
    for literal in ["@example.com", "etag", "uid", "definition", "openapi: 3.0.0", "never copied", "rates-db",
                    "quote-portal", "marketing-site", "people-ops", "source-location", "group:", "user:"]:
        assert literal not in text
    assert snapshot_leaks(text) == []
    assert guard.scan_files(repo, [SNAPSHOT]) == []


def test_fetch_is_idempotent_and_independent_of_entity_order(repo):
    before = (repo / SNAPSHOT).read_text()
    entities = _entities()
    random.Random(11).shuffle(entities)
    _write_export(repo, entities)
    result = commands.fetch_sources(repo, "dev", "backstage")["sources"][0]
    assert result["status"] == "unchanged" and (repo / SNAPSHOT).read_text() == before


def test_experimental_entities_are_skipped_unless_included(repo):
    _configure(repo, f"      file: {EXPORT}\n      include_experimental: true\n")
    added = commands.fetch_sources(repo, "dev", "backstage", dry_run=True)["sources"][0]["added"]
    assert added == ["Component:default/quote-portal"]


def test_names_collisions_fail_and_options_names_resolve_them(repo):
    entities = _entities()
    clone = copy.deepcopy(next(e for e in entities if e["metadata"]["name"] == "pricing-tools"))
    clone["metadata"]["namespace"] = "risk"
    entities.append(clone)
    _write_export(repo, entities)
    with pytest.raises(CatalogError, match="two entries have the name 'pricing-tools'"):
        commands.fetch_sources(repo, "dev", "backstage", dry_run=True)
    _configure(repo, f"      file: {EXPORT}\n      names: {{ \"component:risk/pricing-tools\": risk-pricing-tools }}\n")
    result = commands.fetch_sources(repo, "dev", "backstage", dry_run=True)["sources"][0]
    assert result["added"] == ["Component:risk/pricing-tools"]


def test_a_component_type_without_a_kind_fails_the_fetch(repo):
    _configure(repo, f"      file: {EXPORT}\n      component_types: [website]\n")
    with pytest.raises(CatalogError, match="no catalog kind .* esmeralda.dev/kind"):
        commands.fetch_sources(repo, "dev", "backstage", dry_run=True)


def test_two_groups_for_one_team_fail_the_fetch(repo):
    entities = _entities()
    for e in entities:
        if e["metadata"]["name"] == "ai-coe":
            e["metadata"]["annotations"]["esmeralda.dev/team"] = "cx"
    _write_export(repo, entities)
    with pytest.raises(CatalogError, match="both map to team 'cx'"):
        commands.fetch_sources(repo, "dev", "backstage", dry_run=True)


def test_ref_helpers():
    assert parse_ref("group:default/ai-coe", "group") == ("group", "default", "ai-coe")
    assert parse_ref("ai-coe", "group") == ("group", "default", "ai-coe")
    assert parse_ref("api:risk/pricing", "api") == ("api", "risk", "pricing")
    assert parse_ref("bad ref!", "group") is None
    assert format_ref("api", "default", "x") == "API:default/x"
    assert format_ref("component", "default", "x") == "Component:default/x"


@pytest.mark.parametrize(("options", "expected"), [
    ("      base_url: http://backstage.example.com\n", "must be an https:// URL"),
    ("      base_url: https://user:pw@backstage.example.com\n", "without credentials"),
    ("      base_url: https://backstage.example.com\n      file: x.json\n", "not both"),
    ("      component_types: [agent]\n", "set options.base_url"),
    ("      file: exports/backstage.json\n      placeholders: [tenant]\n", "unknown tenant"),
])
def test_bad_options_fail_the_fetch(repo, options, expected):
    _configure(repo, options)
    with pytest.raises(CatalogError, match=expected):
        commands.fetch_sources(repo, "dev", "backstage", dry_run=True)


def test_unreadable_export_is_unreachable_and_writes_nothing(repo, capsys):
    before = (repo / SNAPSHOT).read_text()
    (repo / EXPORT).write_text("not json")
    assert cli.main(["catalog", "fetch", "--source", "backstage", "--env", "dev", "--repo", str(repo)]) == 2
    assert "can't read the Backstage export" in capsys.readouterr().err
    assert (repo / SNAPSHOT).read_text() == before


def test_placeholders_replace_the_entry_point_domain(repo, monkeypatch):
    monkeypatch.setattr(_live, "unit_outputs", lambda _r, _e, unit: {"entrypoint_domain": "example.com"}
                        if unit == "layer-2-networking" else {})
    _configure(repo, f"      file: {EXPORT}\n      placeholders: [domain]\n")
    commands.fetch_sources(repo, "dev", "backstage")
    entries = {e["id"]: e for e in _snapshot(repo)["entries"]}
    assert entries["API:default/pricing-api"]["inventory"]["deploy"]["endpoint"] == "https://pricing.{domain}"


# ------------------------------------------------------------------------------------------
# fetch: the Catalog API (fake Backstage on 127.0.0.1) and the token
# ------------------------------------------------------------------------------------------


class _FakeBackstage(http.server.BaseHTTPRequestHandler):
    state: dict[str, Any] = {}

    def log_message(self, *_args: Any) -> None:  # keep test output quiet
        pass

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        state = self.state
        state["requests"].append({"path": self.path, "auth": self.headers.get("Authorization")})
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/api/catalog/entities/by-query?filter=kind%3Dgroup")
            self.end_headers()
            return
        if state.get("status", 200) != 200:
            self.send_response(state["status"])
            self.end_headers()
            self.wfile.write(b'{"error": "nope"}')
            return
        if url.path != "/api/catalog/entities/by-query":
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(url.query)
        items = state["items"]
        half = len(items) // 2
        if "cursor" in query:
            assert query["cursor"] == ["page-2"]
            body = {"items": items[half:], "pageInfo": {}, "totalItems": len(items)}
        else:
            state["filters"] = query.get("filter", [])
            body = {"items": items[:half], "pageInfo": {"nextCursor": "page-2"}, "totalItems": len(items)}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def backstage() -> Iterator[dict[str, Any]]:
    """A fake Backstage Catalog API on 127.0.0.1 (the only socket these tests open)."""
    state: dict[str, Any] = {"items": _entities(), "requests": []}
    handler = type("Handler", (_FakeBackstage,), {"state": state})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def test_catalog_api_fetch_pages_with_the_token_and_matches_the_export(repo, backstage, monkeypatch):
    from_export = (repo / SNAPSHOT).read_text()
    monkeypatch.setenv("BACKSTAGE_TEST_TOKEN", TOKEN)
    _configure(repo, f"      base_url: {backstage['url']}/\n      token_env: BACKSTAGE_TEST_TOKEN\n")
    result = commands.fetch_sources(repo, "dev", "backstage")["sources"][0]
    assert result["status"] == "unchanged" and (repo / SNAPSHOT).read_text() == from_export
    assert len(backstage["requests"]) == 2
    assert all(r["auth"] == f"Bearer {TOKEN}" for r in backstage["requests"])
    assert backstage["filters"] == ["kind=component,spec.type=agent", "kind=component,spec.type=mcp-server",
                                    "kind=component,spec.type=service", "kind=api", "kind=group",
                                    "kind=resource,metadata.annotations.esmeralda.dev/kind"]
    assert TOKEN not in (repo / SNAPSHOT).read_text()
    assert TOKEN not in (repo / "catalog/sources.yaml").read_text()


def test_token_is_never_sent_on_a_redirect(repo, backstage, monkeypatch):
    monkeypatch.setenv("BACKSTAGE_TEST_TOKEN", TOKEN)
    src = BackstageSource(name="backstage", catalog_dir=repo / "catalog", root=repo, schema={}, env="dev",
                          spec={"type": "backstage", "options": {"base_url": backstage["url"]}})
    src._get_json(f"{backstage['url']}/moved", backstage["url"], TOKEN)
    assert backstage["requests"][0]["auth"] == f"Bearer {TOKEN}"
    assert backstage["requests"][1]["auth"] is None


@pytest.mark.parametrize(("status", "hint"), [(401, "catalog.entity.read"), (500, "HTTP 500")])
def test_api_errors_are_unreachable_without_the_token(repo, backstage, monkeypatch, capsys, status, hint):
    before = (repo / SNAPSHOT).read_text()
    backstage["status"] = status
    monkeypatch.setenv("ESMERALDA_BACKSTAGE_TOKEN", TOKEN)
    _configure(repo, f"      base_url: {backstage['url']}\n")
    assert cli.main(["catalog", "fetch", "--source", "backstage", "--env", "dev", "--repo", str(repo)]) == 2
    err = capsys.readouterr().err
    assert hint in err and TOKEN not in err
    assert (repo / SNAPSHOT).read_text() == before


def test_unreachable_api_message_has_no_token(repo, monkeypatch):
    with socket.socket() as probe:  # a port nobody listens on
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("ESMERALDA_BACKSTAGE_TOKEN", TOKEN)
    _configure(repo, f"      base_url: http://127.0.0.1:{port}\n")
    with pytest.raises(SourceUnreachable) as exc:
        commands.fetch_sources(repo, "dev", "backstage", dry_run=True)
    assert "can't reach the Backstage catalog API" in str(exc.value) and TOKEN not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_token_from_the_user_env_file(repo, backstage, monkeypatch, tmp_path):
    env_file = tmp_path / "esmeralda-env"
    env_file.write_text(f"ESMERALDA_BACKSTAGE_TOKEN={TOKEN}\n")
    env_file.chmod(0o600)
    monkeypatch.delenv("ESMERALDA_BACKSTAGE_TOKEN", raising=False)
    monkeypatch.setenv("ESMERALDA_ENV_FILE", str(env_file))
    _configure(repo, f"      base_url: {backstage['url']}\n")
    commands.fetch_sources(repo, "dev", "backstage", dry_run=True)
    assert backstage["requests"][0]["auth"] == f"Bearer {TOKEN}"


def test_explicit_token_env_that_is_unset_fails(repo, backstage, monkeypatch, tmp_path):
    monkeypatch.setenv("ESMERALDA_ENV_FILE", str(tmp_path / "missing"))
    monkeypatch.delenv("MY_BACKSTAGE_TOKEN", raising=False)
    _configure(repo, f"      base_url: {backstage['url']}\n      token_env: MY_BACKSTAGE_TOKEN\n")
    with pytest.raises(SourceUnreachable, match="no Backstage token: set MY_BACKSTAGE_TOKEN"):
        commands.fetch_sources(repo, "dev", "backstage", dry_run=True)
    assert backstage["requests"] == []


def test_token_from_secret_manager_read_at_fetch_time(repo, backstage, monkeypatch):
    calls = []
    monkeypatch.setattr(_live, "unit_outputs", lambda _r, _e, unit: {"governance_project_id": "demo-gov-c3d4"})

    def secret_text(_root: Path, resource: str, what: str) -> str:
        calls.append(resource)
        return TOKEN

    monkeypatch.setattr(_live, "secret_text", secret_text)
    _configure(repo, f"      base_url: {backstage['url']}\n"
                     "      token_secret: projects/{project:governance}/secrets/backstage-token/versions/3\n")
    commands.fetch_sources(repo, "dev", "backstage", dry_run=True)
    assert calls == ["projects/demo-gov-c3d4/secrets/backstage-token/versions/3"]
    assert backstage["requests"][0]["auth"] == f"Bearer {TOKEN}"


def test_secret_resource_name_is_validated(repo):
    with pytest.raises(CatalogError, match="not a secret resource name"):
        _live.secret_text(repo, "backstage-token", "read")


# ------------------------------------------------------------------------------------------
# resolve: discovered inventory, profiles, findings, suggestions
# ------------------------------------------------------------------------------------------


def test_entities_are_discovered_inventory_and_never_compiled(repo):
    resolved = catalog.resolve(repo, "dev")
    assert sorted(resolved.inventory) == ["loan-helper", "pricing-api", "pricing-tools", "rates-model"]
    assert resolved.adopted == {}
    assert resolved.provenance["inventory.loan-helper"] == {
        "source": "backstage", "origin": "snapshots/backstage/dev.json#Component:default/loan-helper"}
    assert "callers" not in resolved.inventory["loan-helper"], "consumesApis is never a caller"
    data = json.loads(catalog.render_resolved_file(resolved))
    files_only = json.loads((REPO / "catalog/.resolved/dev.json").read_text())
    for block in COMPILED:
        assert data[block] == files_only[block], f"{block} changed by discovered inventory"
    assert data["inventory_summary"] == {"backstage": {"adopted": 0, "discovered": 4}}


def test_groups_fill_team_profiles_only_where_team_files_leave_them_unset(repo):
    resolved = catalog.resolve(repo, "dev")
    profiles = resolved.document["profiles"]
    assert profiles["ai-coe"]["oncall"] == "ai-coe-oncall", "team.yaml wins"
    record = resolved.provenance["profiles.ai-coe.oncall"]
    assert record["source"] == "catalog-files" and record["shadowed"][0]["source"] == "backstage"
    assert profiles["cx"] == {"team": "cx", "description": "Customer experience squad (synthetic).",
                              "oncall": "cx-pager", "contacts": ["#cx-help"]}
    assert resolved.provenance["profiles.cx.oncall"]["source"] == "backstage"
    files_only = catalog.resolve(REPO, "dev")
    assert resolved.teams == files_only.teams, "Backstage never creates teams nor extends cost centres"


def test_a_group_or_owner_without_a_catalog_team_is_a_finding(repo, capsys):
    entities = _entities()
    entities.append({"kind": "Group", "metadata": {"name": "risk-analytics"}, "spec": {"type": "team"}})
    for e in entities:
        if e["metadata"]["name"] == "pricing-tools":
            e["spec"]["owner"] = "group:default/risk-analytics"
    _refetch(repo, entities)
    problems = _problems(repo)
    assert "Group:default/risk-analytics: maps to team 'risk-analytics', which catalog files don't declare" in problems
    assert "Component:default/pricing-tools: owner maps to team 'risk-analytics'" in problems
    assert "label_mismatch is 'fail'" in problems

    resolved = catalog.resolve(repo, "dev", strict_findings=False)
    assert "owner_team" not in resolved.inventory["pricing-tools"]
    assert cli.main(["catalog", "diff", "--env", "dev", "--json", "--repo", str(repo)]) == 3
    kinds = {(f["kind"], f["subject"]) for f in json.loads(capsys.readouterr().out)["findings"]}
    assert ("metadata_mismatch", "Group:default/risk-analytics") in kinds


def test_consumes_apis_is_a_suggestion_never_a_grant(repo, capsys):
    assert cli.main(["catalog", "diff", "--env", "dev", "--json", "--repo", str(repo)]) == 3
    findings = json.loads(capsys.readouterr().out)["findings"]
    suggestions = [f for f in findings if f["kind"] == "suggested_grant"]
    assert [(f["source"], f["subject"]) for f in suggestions] == [("backstage", "loan-helper")]
    assert "API:default/pricing-api" in suggestions[0]["detail"] and "'pricing-api'" in suggestions[0]["detail"]
    catalog.resolve(repo, "dev")  # suggestions never fail the resolve


@pytest.mark.parametrize(("section", "value", "expected"), [
    ("inventory", {"kind": "a2a", "callers": {"test-vm": {}}}, "may not set inventory.rogue.callers"),
    ("teams", {"cost_centers": ["cc-new"]}, "may not set teams.cx.cost_centers"),
    ("teams_new", {"projects": ["x"]}, "may not set teams.risk.projects"),
    ("profiles", {"repository_paths": ["x"]}, "may not set profiles.cx.repository_paths"),
])
def test_a_backstage_adapter_can_never_set_authorization_data(repo, monkeypatch, section, value, expected):
    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))

    @register_source("backstage")
    class Rogue(BackstageSource):
        def complete(self, layer: Layer, merged: Layer) -> Layer:
            layer = super().complete(layer, merged)
            if section == "inventory":
                layer.inventory["rogue"] = value
            elif section == "teams":
                layer.teams["cx"] = value
            elif section == "teams_new":
                layer.teams["risk"] = value
            else:
                layer.profiles["cx"] = value
            return layer

    assert expected in _problems(repo)


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


# ------------------------------------------------------------------------------------------
# adoption: by backstage pin only
# ------------------------------------------------------------------------------------------

PRICING_FILE = """\
name: pricing
kind: http
owner_team: cx
example: true
deploy: { managed: false, endpoint: "https://pricing.example.com" }
backstage: { source: backstage, ref: "api:default/Pricing-API" }
"""


def test_adoption_by_backstage_pin_fills_description_and_system(repo):
    _write(repo, "external/legacy/pricing.yaml", PRICING_FILE)
    resolved = catalog.resolve(repo, "dev")
    assert resolved.adopted == {"pricing": "backstage"}
    assert "pricing-api" not in resolved.inventory
    target = resolved.targets["pricing"]
    assert target["description"] == "Pricing REST API that no Backstage component provides (synthetic)."
    assert target["system"] == "mortgage"
    assert resolved.provenance["targets.pricing.system"] == {
        "source": "backstage", "origin": "snapshots/backstage/dev.json#API:default/pricing-api", "strategy": "first"}
    assert "callers" not in target and "registry" not in target, "adoption brings no caller and no registry data"
    data = json.loads(catalog.render_resolved_file(resolved))
    assert data["inventory_summary"]["backstage"] == {"adopted": 1, "discovered": 3}


@pytest.mark.parametrize(("change", "expected"), [
    (("https://pricing.example.com", "https://pricing.example.org"), "strict field, every source must agree"),
    (("owner_team: cx", "owner_team: platform"), "targets.pricing.owner_team: backstage"),
    (("Pricing-API", "gone"), "which it doesn't report"),
    (("source: backstage", "source: catalog-files"), "which is a catalog files source"),
])
def test_adoption_conflicts_fail(repo, change, expected):
    _write(repo, "external/legacy/pricing.yaml", PRICING_FILE.replace(*change))
    assert expected in _problems(repo)


def test_a_name_match_without_a_pin_is_an_error(repo):
    _write(repo, "external/legacy/pricing-api.yaml", PRICING_FILE.replace("name: pricing", "name: pricing-api")
           .replace('backstage: { source: backstage, ref: "api:default/Pricing-API" }\n', ""))
    assert "inventory.pricing-api has the name of catalog target 'pricing-api'" in _problems(repo)


def test_a_deployed_target_takes_description_and_system_but_never_an_endpoint(repo):
    entities = _entities()
    entities.append({
        "kind": "Component",
        "metadata": {"name": "ai-coe-mortgage-specialist", "description": "Specialist, per Backstage.",
                     "annotations": {"esmeralda.dev/endpoint": "https://specialist.example.com"}},
        "spec": {"type": "agent", "lifecycle": "production", "owner": "ai-coe", "system": "mortgage"},
    })
    _refetch(repo, entities)
    assert "inventory.ai-coe-mortgage-specialist has the name of catalog target" in _problems(repo)

    path = repo / "catalog/teams/ai-coe/agents/ai-coe-mortgage-specialist.yaml"
    path.write_text(path.read_text()
                    + "backstage: { source: backstage, ref: Component:default/ai-coe-mortgage-specialist }\n")
    resolved = catalog.resolve(repo, "dev")
    target = resolved.targets["ai-coe-mortgage-specialist"]
    assert target["system"] == "mortgage"
    assert "endpoint" not in target["deploy"], "a deployed target's endpoint is its entry point host"
    assert resolved.provenance["targets.ai-coe-mortgage-specialist.description"]["source"] == "catalog-files"
    data = json.loads(catalog.render_resolved_file(resolved))
    files_only = json.loads((REPO / "catalog/.resolved/dev.json").read_text())
    for block in COMPILED:
        assert data[block] == files_only[block], f"{block} changed by a Backstage adoption"


def test_a_backstage_pin_must_name_a_backstage_source(repo, monkeypatch):
    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))

    @register_source("fixture_registry")
    class FixtureRegistry(SnapshotSource):
        CAPABILITIES = {"inventory": ["*"], "targets": ["description", "registry"]}

        def load(self) -> Layer:
            return Layer(source=self.name)

    path = repo / "catalog/sources.yaml"
    path.write_text(path.read_text().replace("\n\nenvironments_dir:",
                                             "\n  - name: registry\n    type: fixture_registry\n\nenvironments_dir:"))
    _write(repo, "external/legacy/pricing.yaml", PRICING_FILE.replace("source: backstage", "source: registry"))
    assert "which is of type 'fixture_registry', not backstage" in _problems(repo)


# ------------------------------------------------------------------------------------------
# joining two sources through one catalog file
# ------------------------------------------------------------------------------------------


def _with_registry(repo: Path, monkeypatch) -> None:
    """Add a second inventory source that also reports a `loan-helper` (an Agent Registry stand-in)."""
    monkeypatch.setattr(catalog_sources, "SOURCE_TYPES", dict(catalog_sources.SOURCE_TYPES))

    @register_source("fixture_registry")
    class FixtureRegistry(SnapshotSource):
        CAPABILITIES = {"inventory": ["*"],
                        "targets": ["deploy.endpoint", "runtime", "runtime_resource", "description", "registry"]}

        def load(self) -> Layer:
            layer = Layer(source=self.name)
            layer.inventory["loan-helper"] = {
                "kind": "a2a", "description": "Loan helper, per the registry.",
                "deploy": {"endpoint": "https://loan-helper.example.com/a2a"}, "runtime": "cloud_run",
                "runtime_resource": "//run.googleapis.com/projects/{project:sandbox}/locations/{region}/services/lh",
                "registry": {"id": "agents/loan-helper", "skills": ["quote"]}}
            layer.origins[("inventory", "loan-helper")] = "snapshots/registry/dev.json#agents/loan-helper"
            return layer

    path = repo / "catalog/sources.yaml"
    text = path.read_text()
    path.write_text(text.replace("  - name: backstage\n",
                                 "  - name: registry\n    type: fixture_registry\n\n  - name: backstage\n"))


def test_two_sources_with_the_same_name_clash_until_a_file_joins_them(repo, monkeypatch):
    _with_registry(repo, monkeypatch)
    assert "backstage: inventory.loan-helper: also reported by registry" in _problems(repo)

    _write(repo, "teams/ai-coe/agents/loan-helper.yaml", """\
name: loan-helper
kind: a2a
owner_team: ai-coe
example: true
deploy:
  managed: false
  endpoint: "https://loan-helper.example.com/a2a"
  registry: { source: registry, id: agents/loan-helper }
backstage: { source: backstage, ref: Component:default/loan-helper }
""")
    resolved = catalog.resolve(repo, "dev")
    assert resolved.adopted == {"loan-helper": "registry"}
    assert resolved.adopted_by == {"loan-helper": ["registry", "backstage"]}
    target = resolved.targets["loan-helper"]
    assert target["description"] == "Loan helper, per the registry.", "first: the earlier source wins"
    assert resolved.provenance["targets.loan-helper.description"]["shadowed"][0]["source"] == "backstage"
    assert target["system"] == "mortgage" and target["runtime"] == "cloud_run"
    assert target["registry"] == {"id": "agents/loan-helper", "skills": ["quote"]}
    assert "loan-helper" not in resolved.inventory
    data = json.loads(catalog.render_resolved_file(resolved))
    assert data["inventory_summary"] == {"backstage": {"adopted": 1, "discovered": 3},
                                         "registry": {"adopted": 1, "discovered": 0}}
    row = catalog.show(resolved, target="loan-helper")["targets"][0]
    assert row["adopted_from"] == "registry, backstage"


# ------------------------------------------------------------------------------------------
# shipped configuration
# ------------------------------------------------------------------------------------------


def test_shipped_source_is_disabled_with_its_capabilities():
    rows = {r["name"]: r for r in commands.describe_sources(REPO, "dev")["sources"]}
    row = rows["backstage"]
    assert row["enabled"] is False and row["type"] == "backstage"
    assert row["capabilities"] == {"inventory": ["*"], "profiles": ["contacts", "description", "oncall"],
                                   "targets": ["deploy.endpoint", "description", "system"]}
    assert not (REPO / SNAPSHOT).exists(), "the public repo commits no real Backstage snapshot"
