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

"""esmeralda.ops: plan summary, log reduction, status parsing, gateway output and the MCP tool schemas.

Everything runs against a synthetic checkout in tmp_path: no Terraform, gcloud or network.
"""

import asyncio
import json
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from esmeralda.ops import contracts, infra, logs, repo, runtime
from esmeralda.ops.repo import OpsError

ENGINE = "projects/demo-cx/locations/us-central1/reasoningEngines/123"

ENV_YAML = """
locals {
  environment    = "dev"
  state_project  = ""
  state_bucket   = ""
  gateways = {
    enabled    = ["envoy"]
    default    = "direct"
    host_rules = { "legacy-dms" = "envoy" } # moved
  }
}
"""

UNITS = [
    "layer-1-projects",
    "layer-2-networking",
    "layer-5-workloads/agents/cx-mortgage-orchestrator",
    "layer-5-workloads/entrypoint",
    "layer-5-workloads/gateways/direct",
    "layer-5-workloads/services/legacy-dms",
    "layer-5-workloads/services/test-vm",
]


def _state(outputs: dict, resources: int = 1, sensitive: tuple = ()) -> dict:
    return {
        "version": 4,
        "outputs": {k: {"value": v, "type": "string", "sensitive": k in sensitive} for k, v in outputs.items()},
        "resources": [{"mode": "managed", "type": "x", "name": f"r{i}"} for i in range(resources)]
        + [{"mode": "data", "type": "y", "name": "d"}],
    }


STATES = {
    "layer-1-projects": _state({"cx_agents_project_id": "demo-cx", "project_suffix": "ab12"}, 3),
    "layer-2-networking": _state({"entrypoint_address": "10.0.1.3", "entrypoint_domain": "example.internal"}, 5),
    "layer-5-workloads/agents/cx-mortgage-orchestrator": _state(
        {"engine_id": ENGINE, "endpoint_url": "https://x/" + ENGINE, "token": "s3cret"}, 4, sensitive=("token",)
    ),
    "layer-5-workloads/entrypoint": _state({"ip_address": "10.0.1.3", "routing": {"default": "direct"}}, 3),
    "layer-5-workloads/gateways/direct": _state(
        {
            "backend_service_id": None,
            "routes": {"legacy-dms": {"host": "legacy-dms.example.internal", "probe": "mcp"}},
        },
        7,
    ),
    "layer-5-workloads/services/legacy-dms": _state({"service_uri": "https://legacy-dms.run.app", "pem": "x" * 500}),
    # test-vm: no state file -> not deployed
}


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    live = tmp_path / "infrastructure" / "live"
    (live / "dev").mkdir(parents=True)
    (live / "root.hcl").write_text("# root\n")
    (live / "dev" / "env.yaml").write_text(ENV_YAML)
    for unit in UNITS:
        (live / "dev" / unit).mkdir(parents=True, exist_ok=True)
        (live / "dev" / unit / "terragrunt.hcl").write_text("")
    (live / "dev" / "layer-5-workloads" / ".terragrunt-cache" / "x").mkdir(parents=True)
    (live / "dev" / "layer-5-workloads" / ".terragrunt-cache" / "x" / "terragrunt.hcl").write_text("")
    for unit, state in STATES.items():
        path = live / ".local_states" / "dev" / unit / "terraform.tfstate"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(state))
    agent = tmp_path / "apps" / "agents" / "cx-mortgage-orchestrator"
    agent.mkdir(parents=True)
    (agent / "agent.yaml").write_text("name: cx-mortgage-orchestrator\nframework: google-adk\n")
    doc = tmp_path / contracts.GATEWAY_DOC
    doc.parent.mkdir(parents=True)
    doc.write_text(
        textwrap.dedent(
            """\
            # Gateways
            ## Specs
            ### 1. The Gateway Contract

            A gateway outputs `backend_service_id` and `routes`. See [`direct/outputs.tf`](../../x.tf).

            ### 2. The `gateways` Block in `env.yaml`

            `enabled`, `default`, `host_rules`.
            ### 3. The Entry Point
            other
            """
        )
    )
    monkeypatch.delenv(repo.REPO_ENV_VAR, raising=False)
    monkeypatch.chdir(live / "dev")
    return tmp_path


# ---- repository -----------------------------------------------------------------------------


def test_repo_root_from_cwd_and_env_var(checkout, monkeypatch, tmp_path_factory):
    assert repo.repo_root() == checkout.resolve()
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    with pytest.raises(OpsError, match="ESMERALDA_REPO"):
        repo.repo_root()
    monkeypatch.setenv(repo.REPO_ENV_VAR, str(checkout))
    assert repo.repo_root() == checkout.resolve()
    monkeypatch.setenv(repo.REPO_ENV_VAR, str(checkout / "apps"))
    with pytest.raises(OpsError, match="not an Esmeralda checkout"):
        repo.repo_root()


def test_units_skip_caches_and_validate(checkout):
    assert repo.units(checkout, "dev") == sorted(UNITS)
    assert repo.backend(checkout, "dev") == "local"
    with pytest.raises(OpsError, match="Unknown env 'qa'. Known: dev"):
        repo.units(checkout, "qa")
    with pytest.raises(OpsError, match="Did you mean: layer-5-workloads/entrypoint"):
        repo.unit_dir(checkout, "dev", "entrypoint")
    with pytest.raises(OpsError, match="Unknown unit"):
        repo.unit_dir(checkout, "dev", "../../etc")


def test_gateways_block(checkout):
    assert repo.gateways_block(checkout, "dev") == {
        "enabled": ["envoy"],
        "default": "direct",
        "host_rules": {"legacy-dms": "envoy"},
    }


def test_governance_block_and_apigee_unit_enabled(checkout):
    env_yaml = checkout / "infrastructure/live/dev/env.yaml"
    apigee_units = [
        "layer-5-workloads/apigee/platform",
        "layer-5-workloads/apigee/products",
        "layer-5-workloads/apigee/capabilities/partner-agents",
        "layer-5-workloads/apigee/capabilities/token-quota",
        "layer-5-workloads/apigee/capabilities/cross-cloud",
    ]
    for unit in apigee_units:
        (checkout / "infrastructure/live/dev" / unit).mkdir(parents=True, exist_ok=True)
        (checkout / "infrastructure/live/dev" / unit / "terragrunt.hcl").write_text("")

    # 1. With apigee.enabled = false, all apigee units are disabled/excluded.
    env_yaml.write_text(
        ENV_YAML
        + """
locals {
  governance = {
    apigee = {
      enabled = false
    }
    capabilities = {
      partner_agents = "apigee"
      token_quota    = "agent_gateway"
      cross_cloud    = "apigee"
    }
  }
}
"""
    )
    gov = repo.governance_block(checkout, "dev")
    assert gov["apigee"]["enabled"] is False
    for unit in apigee_units:
        assert infra.unit_enabled(checkout, "dev", unit) is False

    # 2. With apigee.enabled = true, platform/products and capabilities set to "apigee" are enabled.
    env_yaml.write_text(env_yaml.read_text().replace("enabled = false", "enabled = true"))
    gov_enabled = repo.governance_block(checkout, "dev")
    assert gov_enabled["apigee"]["enabled"] is True
    assert infra.unit_enabled(checkout, "dev", "layer-5-workloads/apigee/platform") is True
    assert infra.unit_enabled(checkout, "dev", "layer-5-workloads/apigee/products") is True
    assert infra.unit_enabled(checkout, "dev", "layer-5-workloads/apigee/capabilities/partner-agents") is True
    assert infra.unit_enabled(checkout, "dev", "layer-5-workloads/apigee/capabilities/cross-cloud") is True
    assert infra.unit_enabled(checkout, "dev", "layer-5-workloads/apigee/capabilities/token-quota") is False


def test_gcs_backend_detected(checkout):
    env_yaml = checkout / "infrastructure/live/dev/env.yaml"
    env_yaml.write_text(
        ENV_YAML.replace('state_project  = ""', 'state_project  = "p"').replace(
            'state_bucket   = ""', 'state_bucket   = "b"'
        )
    )
    assert repo.backend(checkout, "dev") == "gcs"


def test_hints_and_error_lines():
    assert "gcloud auth login" in repo.hint("ERROR: Reauthentication required.")
    assert "application-default login" in repo.hint("Could not find default credentials")
    assert "ESMERALDA_BILLING_ACCOUNT" in repo.hint('Error: get_env: "ESMERALDA_BILLING_ACCOUNT" not set')
    assert repo.hint("all good") is None
    text = "noise\n\x1b[31m│ Error: boom\x1b[0m\n│\nmore noise\n"
    assert repo.error_lines(text) == ["Error: boom"]
    failed = repo.failure("terragrunt plan", subprocess.CompletedProcess([], 1, "", "Error: invalid_grant"))
    assert "gcloud auth expired" in str(failed) and "exit 1" in str(failed)


def test_run_reports_missing_binary(checkout):
    with pytest.raises(OpsError, match="not found on PATH"):
        repo.run(["definitely-not-a-binary-xyz"], cwd=checkout, timeout=5, what="x")


# ---- status ---------------------------------------------------------------------------------


def test_parse_state_drops_sensitive_and_counts_managed():
    state = repo.parse_state("u", STATES["layer-5-workloads/agents/cx-mortgage-orchestrator"])
    assert state.deployed and state.resources == 4
    assert "token" not in state.outputs and state.sensitive == ["token"]
    empty = repo.parse_state("u", {"outputs": {}, "resources": []})
    assert empty.deployed is False and empty.resources == 0


def test_status_summary(checkout):
    result = infra.status(checkout, "dev")
    assert result["deployed"] == 6 and result["units"] == 7
    assert result["not_deployed"] == ["layer-5-workloads/services/test-vm"]
    assert result["projects"] == {"cx_agents": "demo-cx"}
    assert result["entry_point"] == {"ip": "10.0.1.3", "domain": "example.internal", "routing": {"default": "direct"}}
    assert result["gateways"]["direct"]["routes"] == ["legacy-dms.example.internal (mcp)"]
    assert result["agents"]["cx-mortgage-orchestrator"]["engine_id"] == ENGINE
    assert result["mcp_services"] == {"legacy-dms": "https://legacy-dms.run.app"}
    assert "test_vm" not in result
    assert "s3cret" not in json.dumps(result)


def test_status_of_one_unit_summarises_blobs(checkout):
    result = infra.status(checkout, "dev", "layer-5-workloads/services/legacy-dms")
    assert result["outputs"]["pem"] == "<str, 502 chars>"
    assert result["outputs"]["service_uri"] == "https://legacy-dms.run.app"


def test_list_units(checkout):
    result = infra.list_units(checkout, "dev")
    by_unit = {u["unit"]: u for u in result["units"]}
    assert by_unit["layer-5-workloads/gateways/direct"] == {
        "unit": "layer-5-workloads/gateways/direct",
        "layer": 5,
        "deployed": True,
        "local_state": True,
        "resources": 7,
        "enabled": True,
        "excluded": False,
        "dependencies": [],
    }
    assert by_unit["layer-5-workloads/services/test-vm"]["deployed"] is False


def test_parse_output_json():
    state = repo.parse_output_json("u", {"a": {"value": 1}, "b": {"value": "x", "sensitive": True}})
    assert state.outputs == {"a": 1} and state.sensitive == ["b"] and state.resources is None


# ---- plan -----------------------------------------------------------------------------------

PLAN = {
    "format_version": "1.2",
    "resource_changes": [
        {"address": "google_a.keep", "change": {"actions": ["no-op"], "before": {"x": 1}, "after": {"x": 1}}},
        {"address": "google_a.new", "change": {"actions": ["create"], "before": None, "after": {"x": 1}}},
        {
            "address": "google_compute_url_map.this",
            "change": {
                "actions": ["update"],
                "before": {"name": "m", "host_rule": [{"hosts": ["a"]}], "labels": {"a": "1"}, "fingerprint": "f1"},
                "after": {"name": "m", "host_rule": [{"hosts": ["a", "b"]}], "labels": {"a": "1"}},
                "after_unknown": {"fingerprint": True, "labels": {}},
            },
        },
        {
            "address": "module.x.google_b.swap",
            "change": {
                "actions": ["delete", "create"],
                "before": {"region": "r1", "id": "1"},
                "after": {"region": "r2"},
                "after_unknown": {"id": True},
                "replace_paths": [["region"]],
            },
        },
        {"address": "google_c.gone", "change": {"actions": ["delete"], "before": {"x": 1}, "after": None}},
        {"address": "data.google_d.read", "change": {"actions": ["read"], "before": None, "after": {}}},
    ],
    "output_changes": {"ip": {"actions": ["no-op"]}, "routing": {"actions": ["update"]}},
    "resource_drift": [{"address": "google_a.keep", "change": {"actions": ["update"]}}],
}


def test_summarize_plan():
    result = infra.summarize_plan(PLAN)
    assert result["changes"] == 4
    assert result["counts"] == {"add": 1, "change": 1, "destroy": 1, "replace": 1, "read": 1}
    assert result["resources"] == [
        "-/+ module.x.google_b.swap: id, region [forces replacement: region]",
        "- google_c.gone",
        "~ google_compute_url_map.this: fingerprint, host_rule",
        "+ google_a.new",
        "<= data.google_d.read",
    ]
    assert result["output_changes"] == ["routing"]
    assert result["drift"] == ["google_a.keep (update)"]


def test_summarize_plan_no_changes_and_truncation():
    assert infra.summarize_plan({"resource_changes": []}) == {"changes": 0, "counts": {}, "resources": []}
    many = {"resource_changes": [{"address": f"a.r{i}", "change": {"actions": ["create"]}} for i in range(70)]}
    result = infra.summarize_plan(many, max_lines=10)
    assert len(result["resources"]) == 10 and result["truncated"] == 60


def test_plan_unit_is_read_only_and_summarised(checkout, monkeypatch):
    calls = []

    def fake_run(cmd, *, cwd, timeout, what):
        calls.append(cmd)
        if "plan" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "Plan: 1 to add", "")
        return subprocess.CompletedProcess(cmd, 0, "INFO log line\n" + json.dumps(PLAN), "")

    monkeypatch.setattr(repo, "run", fake_run)
    result = infra.plan_unit(checkout, "dev", "layer-5-workloads/entrypoint/")
    plan_cmd = calls[0]
    assert "-lock=false" in plan_cmd and "apply" not in plan_cmd
    out = [a for a in plan_cmd if a.startswith("-out=")][0]
    assert out.endswith(".esmeralda/plans/dev/layer-5-workloads_entrypoint.tfplan")
    assert calls[1][:4] == ["terragrunt", "--non-interactive", "show", "-json"]
    assert result["unit"] == "layer-5-workloads/entrypoint" and result["changes"] == 4
    assert result["plan_file"] == ".esmeralda/plans/dev/layer-5-workloads_entrypoint.tfplan"


def test_plan_unit_failure_is_actionable(checkout, monkeypatch):
    monkeypatch.setattr(
        repo,
        "run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, "", "Error: oauth2: invalid_grant reauth related error"),
    )
    with pytest.raises(OpsError, match="gcloud auth expired"):
        infra.plan_unit(checkout, "dev", "layer-5-workloads/entrypoint")


def test_plan_dir_is_gitignored():
    gitignore = Path(__file__).resolve().parents[3] / ".gitignore"
    if gitignore.is_file():
        assert ".esmeralda/" in gitignore.read_text().splitlines()


# ---- logs -----------------------------------------------------------------------------------

TRACEBACK = """Traceback (most recent call last):
  File "/app/agent.py", line 10, in run
    result = call()
             ^^^^^^
  File "/app/tools.py", line 3, in call
    raise ValueError("bad income id 42")
ValueError: bad income id 42"""


def test_collapse_traceback_and_plain_messages():
    assert logs.collapse(TRACEBACK) == ("ValueError: bad income id 42", True)
    message, was = logs.collapse("Tool failed\n" + TRACEBACK)
    assert was and message == "Tool failed → ValueError: bad income id 42"
    assert logs.collapse("one\n  two ") == ("one two", False)
    grpc = (
        'Traceback (most recent call last):\n  File "a.py", line 1, in f\n    g()\n'
        'grpc.RpcError: <RpcError that terminated with:\n\tstatus = PERMISSION_DENIED\n\tdetails = "denied"\n>'
    )
    assert logs.collapse(grpc) == (
        'grpc.RpcError: <RpcError that terminated with: status = PERMISSION_DENIED details = "denied" >',
        True,
    )
    assert logs.is_frame_only('  File "/app/x.py", line 3, in f')
    assert logs.is_frame_only("Traceback (most recent call last):")
    assert not logs.is_frame_only("ValueError: boom")


def test_message_of_payload_shapes():
    assert logs.message_of({"message": "m", "x": 1}) == "m"
    assert logs.message_of({"error": {"message": "e"}}) == "e"
    assert logs.message_of({"a": 1}) == '{"a":1}'
    assert logs.message_of(None) == ""


def test_reduce_entries_dedupes_collapses_and_limits():
    entries = [
        {"time": "t5", "severity": "ERROR", "payload": TRACEBACK},
        {"time": "t4", "severity": "WARNING", "payload": {"message": "slow call 1200 ms to 10.0.0.1"}},
        {"time": "t3", "severity": "WARNING", "payload": {"message": "slow call 900 ms to 10.0.0.2"}},
        {"time": "t2", "severity": "ERROR", "payload": '  File "/app/x.py", line 1, in f'},
        {"time": "t1", "severity": "WARNING", "payload": "x" * 400},
    ]
    result = logs.reduce_entries(entries, limit=50)
    assert result["scanned"] == 5 and result["frame_lines_dropped"] == 1 and result["tracebacks_collapsed"] == 1
    first, second, third = result["entries"]
    assert first == {"time": "t5", "severity": "ERROR", "message": "ValueError: bad income id 42"}
    assert second["count"] == 2 and second["time"] == "t4" and second["first"] == "t3"
    assert len(third["message"]) == 300 and third["message"].endswith("…")
    limited = logs.reduce_entries(entries, limit=1)
    assert limited["returned"] == 1 and limited["more_distinct"] == 2


def test_since_severity_and_filter():
    assert logs.parse_since("30m").total_seconds() == 1800
    assert logs.parse_since("2h").total_seconds() == 7200
    with pytest.raises(OpsError, match="since"):
        logs.parse_since("yesterday")
    assert logs.parse_severity("warn") == "WARNING"
    with pytest.raises(OpsError, match="severity"):
        logs.parse_severity("LOUD")
    project, filter_ = logs.build_filter(ENGINE, "WARNING", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    assert project == "demo-cx"
    assert filter_ == (
        'resource.type="aiplatform.googleapis.com/ReasoningEngine" AND resource.labels.reasoning_engine_id="123"'
        ' AND timestamp>="2026-01-02T03:04:05Z" AND severity>=WARNING'
    )
    assert "severity" not in logs.build_filter(ENGINE, "DEFAULT", datetime.now(UTC))[1]


def test_agent_logs_with_gcloud_fallback(checkout, monkeypatch):
    rows = [
        {"timestamp": "2026-01-02T03:04:05.123456Z", "severity": "ERROR", "textPayload": TRACEBACK},
        {"timestamp": "2026-01-02T03:04:04Z", "severity": "WARNING", "jsonPayload": {"message": "retry"}},
    ]
    seen = {}

    def fake_run(cmd, **_):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")

    monkeypatch.setattr(logs, "_client_available", lambda: False)
    monkeypatch.setattr(repo, "run", fake_run)
    result = logs.agent_logs(checkout, "dev", "cx-mortgage-orchestrator", since="1h", limit=5)
    assert seen["cmd"][:3] == ["gcloud", "logging", "read"] and "--project=demo-cx" in seen["cmd"]
    assert "--limit=50" in seen["cmd"]
    assert result["source"] == "gcloud" and result["engine"] == "123"
    assert result["entries"][0] == {
        "time": "2026-01-02T03:04:05Z",
        "severity": "ERROR",
        "message": "ValueError: bad income id 42",
    }


def test_agent_logs_unknown_agent(checkout):
    with pytest.raises(OpsError, match="Unknown unit"):
        logs.agent_logs(checkout, "dev", "nope")


# ---- runtime --------------------------------------------------------------------------------

GATEWAY_OUTPUT = """🔎 Probing 3 host(s) through the entry point 10.0.1.3 (*.example.internal) from test-vm-dev...
  ✅ legacy-dms.example.internal                        mcp   HTTP 200
  ✅ specialist.example.internal                        a2a   HTTP 200 (DNS still resolves to 10.0.1.9)
  ❌ broken.example.internal                            mcp   HTTP 404  {"message":"no Route matched"}
❌ Some routes failed through the entry point (see above).
"""


def test_parse_gateway_output():
    parsed = runtime.parse_gateway_output(GATEWAY_OUTPUT)
    assert parsed["entry_point"] == {"ip": "10.0.1.3", "domain": "example.internal"}
    assert parsed["hosts"] == [
        {"host": "legacy-dms.example.internal", "probe": "mcp", "ok": True, "http": 200},
        {"host": "specialist.example.internal", "probe": "a2a", "ok": True, "http": 200, "dns_stale": "10.0.1.9"},
        {
            "host": "broken.example.internal",
            "probe": "mcp",
            "ok": False,
            "http": 404,
            "error": '{"message":"no Route matched"}',
        },
    ]


def test_test_gateway_summary(checkout, monkeypatch):
    (checkout / "scripts").mkdir()
    (checkout / "scripts" / "test_gateway.sh").write_text("")
    monkeypatch.setattr(repo, "run", lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, GATEWAY_OUTPUT, ""))
    result = runtime.test_gateway(checkout, "dev")
    assert result["passed"] is False and result["ok"] == 2 and result["failed"] == 1
    assert result["error"] == "❌ Some routes failed through the entry point (see above)."


def test_query_agent_uses_engine_and_captures_report(checkout, monkeypatch):
    from esmeralda import query as q

    seen = {}

    async def fake_run(config, request, report, *, url=None, engine=None):
        seen.update(config=config, request=request, engine=engine)
        report.adk_event({"author": "root", "content": {"parts": [{"function_call": {"name": "lookup", "args": {}}}]}})
        report.adk_event({"author": "root", "content": {"parts": [{"text": "Income verified."}]}})

    monkeypatch.setattr(q, "run", fake_run)
    result = asyncio.run(runtime.query_agent(checkout, "dev", "cx-mortgage-orchestrator", "hi"))
    assert seen["engine"] == ENGINE and seen["request"].message == "hi"
    assert result["passed"] is True and result["answer"] == "Income verified."
    assert result["tool_calls"] == ["lookup"] and result["agents"] == ["root"] and result["engine"] == "123"


def test_query_agent_failure_is_reported(checkout, monkeypatch):
    from esmeralda import query as q

    async def failing(*_args, **_kwargs):
        raise RuntimeError("HTTP 403 from https://x: denied")

    monkeypatch.setattr(q, "run", failing)
    result = asyncio.run(runtime.query_agent(checkout, "dev", "cx-mortgage-orchestrator", "hi"))
    assert result["passed"] is False and "permission denied" in result["errors"][0]
    with pytest.raises(OpsError, match="Unknown agent"):
        asyncio.run(runtime.query_agent(checkout, "dev", "nope", "hi"))


# ---- contracts ------------------------------------------------------------------------------


def test_describe_contracts(checkout):
    result = contracts.describe_contracts(checkout)
    assert result["gateway_contract"] == "A gateway outputs `backend_service_id` and `routes`. See [`direct/outputs.tf`]."
    assert result["gateways_block"] == "`enabled`, `default`, `host_rules`."
    assert result["envs"]["dev"]["host_rules"] == {"legacy-dms": "envoy"}
    assert result["gateway_units"] == {"dev": ["direct"]}


# ---- MCP server -----------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "status": {"env"},
    "list_units": {"env"},
    "plan_unit": {"env", "unit"},
    "query_agent": {"env", "agent", "message"},
    "test_gateway": {"env"},
    "agent_logs": {"env", "agent"},
    "describe_contracts": set(),
}


def test_tool_schemas_and_calls(checkout):
    fastmcp = pytest.importorskip("fastmcp", reason="fastmcp is locked via the workspace MCP services")
    from esmeralda.ops.server import build_server

    async def scenario():
        async with fastmcp.Client(build_server(checkout)) as client:
            tools = {t.name: t for t in await client.list_tools()}
            assert set(tools) == set(EXPECTED_TOOLS)
            for name, required in EXPECTED_TOOLS.items():
                tool = tools[name]
                assert set(tool.input_schema.get("required", [])) == required, name
                assert tool.annotations.read_only_hint is True and tool.annotations.destructive_hint is False
                assert tool.description
            assert not any(word in name for name in tools for word in ("apply", "destroy", "delete"))
            logs_props = tools["agent_logs"].input_schema["properties"]
            assert logs_props["since"]["default"] == "30m" and logs_props["severity"]["default"] == "WARNING"
            assert logs_props["limit"]["default"] == 50

            ok = await client.call_tool("list_units", {"env": "dev"})
            text = ok.content[0].text
            assert json.loads(text)["backend"] == "local" and "\n" not in text  # compact JSON
            bad = await client.call_tool("status", {"env": "qa"}, raise_on_error=False)
            assert bad.is_error and "Unknown env 'qa'" in bad.content[0].text

    asyncio.run(scenario())


def test_cli_mcp_reports_missing_checkout(tmp_path, monkeypatch, capsys):
    from esmeralda import cli

    monkeypatch.delenv(repo.REPO_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["mcp"]) == 2
    assert "No Esmeralda checkout" in capsys.readouterr().err
