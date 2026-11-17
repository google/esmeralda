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

"""Runtime checks: query a deployed agent, probe every gateway route.

``query_agent`` takes the same path as ``make query AGENT=<agent> TARGET=remote``: the engine id
from the agent unit's ``engine_id`` output, ``agent.yaml`` from ``apps/agents/<agent>``, then
``esmeralda.query.run(..., engine=...)``, with the report captured instead of printed.

``test_gateway`` resolves the test VM and the catalog routes (``esmeralda.ops.testvm``), runs
``scripts/vm/gateway_probes.sh`` on the VM over IAP SSH and parses its per-host lines.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from esmeralda.ops import repo, testvm
from esmeralda.ops.repo import OpsError

AGENTS_DIR = Path("apps/agents")
ANSWER_LIMIT = 4000


def agents(root: Path) -> list[str]:
    base = root / AGENTS_DIR
    return sorted(p.name for p in base.iterdir() if (p / "agent.yaml").is_file()) if base.is_dir() else []


def report_result(report: Any, *, fail_on_tool_error: bool = False) -> dict[str, Any]:
    """The compact result of an ``esmeralda.report.Report``."""
    problems = report.failures(fail_on_tool_error=fail_on_tool_error)
    answer = report.answer or ""
    result: dict[str, Any] = {
        "passed": not problems,
        "answer": answer if len(answer) <= ANSWER_LIMIT else answer[: ANSWER_LIMIT - 1] + "…",
        "answer_author": report.answer_author,
        "agents": report.authors,
        "tool_calls": report.tool_names,
        "tool_errors": report.tool_errors,
        "tokens": report.tokens,
        "seconds": round(time.monotonic() - report.started, 1),
    }
    if report.task_state:
        result["task_state"] = report.task_state
    if problems:
        result["problems"] = problems
    if report.errors:
        result["errors"] = [repo.short(e, 300) for e in report.errors[:5]]
    return result


async def query_agent(
    root: Path, env: str, agent: str, message: str, *, timeout: float = 300.0, session: str | None = None
) -> dict[str, Any]:
    from esmeralda import query as q
    from esmeralda.config import AgentConfig
    from esmeralda.report import Report

    known = agents(root)
    if agent not in known:
        raise OpsError(f"Unknown agent '{agent}'. Known: {', '.join(known)}")
    unit = f"layer-5-workloads/agents/{agent}"
    repo.unit_dir(root, env, unit)
    engine = repo.output(root, env, unit, "engine_id")
    if not engine:
        raise OpsError(f"No engine_id output for {agent} in {env}: deploy it (esmeralda deploy agents/{agent} --env {env})")
    config = AgentConfig.load(root / AGENTS_DIR / agent)

    # Never print: on the stdio transport, stdout is the MCP channel.
    report = Report(stream=io.StringIO(), color=False)
    try:
        await q.run(config, q.Query(message=message, session_id=session, timeout=timeout), report, engine=engine)
    except Exception as exc:  # transport and agent failures are a failed query, with a hint
        text = f"{type(exc).__name__}: {exc}"
        if "print-access-token" in text or "gcloud" in text:
            text += " (gcloud auth expired? run `gcloud auth login` and `gcloud auth application-default login`)"
        elif found := repo.hint(text):
            text += f" ({found})"
        report.errors.append(text)
    return {"env": env, "agent": agent, "engine": engine.rsplit("/", 1)[-1], **report_result(report)}


# ------------------------------------------------------------------------------------------
# test_gateway
# ------------------------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_HOST = re.compile(
    r"^\s*(?P<mark>✅|❌)\s+(?P<host>\S+)\s+(?P<probe>\S+)"
    r"(?:\s+(?P<token>\S+)\s+(?P<backend>\S+))?\s+HTTP\s+(?P<code>\d{3})"
    r"(?:\s+\(DNS still resolves to (?P<dns>[^)]+)\))?\s*(?P<rest>.*)$"
)
_PROBING = re.compile(r"Probing (?P<n>\d+) host\(s\) through the entry point (?P<ip>\S+) \(\*\.(?P<domain>[^)]+)\)")
_PARTNER_PROBE = re.compile(
    r"^\s*(?P<mark>✅|❌)\s+Probe\s+(?P<num>\d+)(?:\s*\((?P<label>[^)]+)\))?[:\s]+(?P<detail>.*)$"
)


def parse_gateway_output(text: str) -> dict[str, Any]:
    """Per-host results from the output of the gateway probes (``run_gateway_probes``)."""
    hosts, entry = [], {}
    for raw in text.splitlines():
        line = _ANSI.sub("", raw)
        if match := _PROBING.search(line):
            entry = {"ip": match["ip"], "domain": match["domain"]}
            continue
        match = _HOST.match(line)
        if not match:
            continue
        host: dict[str, Any] = {
            "host": match["host"],
            "probe": match["probe"],
            "ok": match["mark"] == "✅",
            "http": int(match["code"]),
        }
        if match["dns"]:
            host["dns_stale"] = match["dns"]
        if not host["ok"] and match["rest"].strip():
            host["error"] = repo.short(match["rest"], 200)
        hosts.append(host)
    return {"entry_point": entry, "hosts": hosts}


GATEWAY_PROBES = Path("scripts/vm/gateway_probes.sh")
QUERY_ENV_VAR = "TEST_GATEWAY_QUERY"


def gateway_routes(root: Path, env: str) -> list[dict[str, str]]:
    """``host``/``probe``/``token``/``backend`` of every catalog host (entry point ``routes`` output)."""
    l5_dir = testvm.env_rel(env, "layer-5-workloads")
    routes = testvm.outputs(root, env, "layer-5-workloads/entrypoint").get("routes")
    if not isinstance(routes, dict):
        raise OpsError(
            f"No routes output in {l5_dir}/entrypoint. Deploy the entry point first (make deploy-entrypoint ENV={env})."
        )
    keys = ("host", "probe", "token", "backend")
    found = [{k: "null" if route.get(k) is None else testvm.text(route[k]) for k in keys} for route in routes.values()]
    if not found:
        raise OpsError(f"The entry point serves no host (catalog in {testvm.env_rel(env, 'env.yaml')}).")
    return found


def attribution_vars(root: Path, env: str, routes: list[dict[str, str]]) -> dict[str, str]:
    """Inputs of the verified attribution probes (``catalog/.resolved/<env>.json`` ``attribution``).

    * unregistered caller: the layer-3 probe identity (in no catalog entry) calls every mcp/a2a host;
    * declared mismatch: the test VM declares a cost centre of another team on every MCP host whose
      caller guard is on.
    """
    try:
        resolved = json.loads((root / "catalog" / ".resolved" / f"{env}.json").read_text())
        attribution = (resolved.get("attribution") if isinstance(resolved, dict) else None) or {}
    except (OSError, ValueError):
        attribution = {}
    policy = attribution.get("policy") or {}
    teams = [t for t in attribution.get("principal_teams") or [] if isinstance(t, dict)]
    vm_teams = [t for t in teams if t.get("caller") == "test-vm"]
    vm_ccs = {cc for t in vm_teams for cc in t.get("cost_centers") or []}
    others = [t for t in teams if t.get("caller") != "test-vm"]
    foreign = [cc for t in others for cc in t.get("cost_centers") or [] if cc not in vm_ccs]

    def guarded(host: str) -> bool:
        unit = f"layer-5-workloads/services/{host.split('.')[0]}"
        return testvm.text(testvm.outputs(root, env, unit).get("caller_guard")) == "true"

    guarded_hosts = [r["host"] for r in routes if r["probe"] == "mcp" and guarded(r["host"])]
    return {
        "PROBE_SA": testvm.text(testvm.outputs(root, env, testvm.SECURITY_UNIT).get("probe_unregistered_sa_email")),
        "MISMATCH_MODE": testvm.text(policy.get("declared_mismatch")) or "reject",
        "UNMAPPED_MODE": testvm.text(policy.get("unmapped_caller")) or "deny",
        "VM_TEAM": testvm.text(vm_teams[0].get("team")) if vm_teams else "",
        "FOREIGN_CC": testvm.text(foreign[0]) if foreign else "",
        "GUARDED_HOSTS": "".join(f" {h}" for h in guarded_hosts),
    }


def run_gateway_probes(root: Path, env: str, *, timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    """Resolves the test VM and the catalog routes, then runs ``scripts/vm/gateway_probes.sh`` on the VM.

    The output keeps the format ``parse_gateway_output`` reads: a ``Probing`` header, one line per
    probe, then a final ``✅``/``❌`` line (a resolution error is a single ``❌`` line, exit 1).
    """
    script = root / GATEWAY_PROBES
    if not script.is_file():
        raise OpsError(f"{GATEWAY_PROBES} not found in this checkout")
    cmd = ["gateway-probes", env]
    try:
        vm = testvm.resolve(root, env)
        routes = gateway_routes(root, env)
        message = os.environ.get(QUERY_ENV_VAR) or DEFAULT_VERIFY_MESSAGE
        query_body = {"class_method": "async_stream_query", "input": {"user_id": "test-gateway", "message": message}}
        variables = {
            "ENTRY_IP": vm.entry_ip,
            "ROUTES": "\n".join(f"{r['host']} {r['probe']} {r['token']} {r['backend']}" for r in routes),
            "CA_PEM": vm.ca_pem,
            "QUERY_BODY": json.dumps(query_body, separators=(",", ":"), ensure_ascii=False),
            **attribution_vars(root, env, routes),
        }
    except OpsError as exc:
        return subprocess.CompletedProcess(cmd, 1, "", f"❌ {exc}\n")
    header = (
        f"🔎 Probing {len(routes)} host(s) through the entry point {vm.entry_ip} (*.{vm.domain}) from {vm.name}...\n"
    )
    result = testvm.run(vm, script.read_text(), vars=variables, timeout=timeout)
    if result.returncode == 0:
        return subprocess.CompletedProcess(
            cmd, 0, header + result.stdout + "✅ Every route answered through the entry point.\n", result.stderr
        )
    stderr = result.stderr + "❌ Some routes failed through the entry point (see above).\n"
    return subprocess.CompletedProcess(cmd, result.returncode, header + result.stdout, stderr)


def test_gateway(
    root: Path,
    env: str,
    *,
    probe: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    repo.env_dir(root, env)
    started = time.monotonic()
    result = run_gateway_probes(root, env, timeout=timeout)
    parsed = parse_gateway_output(result.stdout + "\n" + result.stderr)
    hosts = parsed["hosts"]
    if probe:
        needle = probe.strip().lower()
        hosts = [
            h
            for h in hosts
            if needle in str(h.get("host", "")).lower() or needle == str(h.get("probe", "")).lower()
        ]
        parsed["hosts"] = hosts
        passed = bool(hosts) and all(h["ok"] for h in hosts)
    else:
        passed = result.returncode == 0
    summary: dict[str, Any] = {
        "env": env,
        "passed": passed,
        "probe_filter": probe,
        "ok": sum(h["ok"] for h in hosts),
        "failed": sum(not h["ok"] for h in hosts),
        **parsed,
        "seconds": round(time.monotonic() - started, 1),
    }
    if not passed:
        text = f"{result.stderr}\n{result.stdout}"
        fatal = [line.strip() for line in _ANSI.sub("", text).splitlines() if line.strip().startswith("❌ ")]
        if probe and not hosts:
            summary["error"] = f"No gateway host matched --probe '{probe}'"
        else:
            summary["error"] = repo.short(fatal[-1] if fatal else " | ".join(repo.error_lines(text, 4)), 300)
        if found := repo.hint(text):
            summary["hint"] = found
    return summary


def parse_partner_output(text: str) -> dict[str, Any]:
    """Per-probe results from the output of the partner probes (``run_partner_probes``)."""
    probes: list[dict[str, Any]] = []
    mode = "live"
    for raw in text.splitlines():
        line = _ANSI.sub("", raw)
        if "offline" in line.lower():
            mode = "offline"
        match = _PARTNER_PROBE.match(line)
        if not match:
            continue
        entry: dict[str, Any] = {
            "probe": int(match["num"]),
            "ok": match["mark"] == "✅",
            "detail": repo.short(match["detail"].strip(), 200),
        }
        if match["label"]:
            entry["label"] = match["label"].strip()
        probes.append(entry)
    return {"mode": mode, "probes": probes}


PARTNER_FILES = {"SCENARIOS": Path("tests/partner/scenarios.py"), "PROBE": Path("tests/partner/probe.py")}
QUOTA_VAR = "ESMERALDA_TEST_QUOTA_EXHAUSTION"

# Runs ON the test VM: writes the CA and the shipped catalog files, then runs probe.py (stdlib only).
PARTNER_REMOTE = r"""set -euo pipefail
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
printf '%s\n' "${CA_PEM}" > "${WORK}/ca.pem"
printf '%s' "${SCENARIOS_B64}" | base64 -d > "${WORK}/scenarios.py"
printf '%s' "${PROBE_B64}" | base64 -d > "${WORK}/probe.py"
PARTNER_CA_FILE="${WORK}/ca.pem"
export PARTNER_ENTRY_IP PARTNER_HOST PARTNER_API_KEY PARTNER_CA_FILE ESMERALDA_TEST_QUOTA_EXHAUSTION
python3 "${WORK}/probe.py"
"""


def run_partner_probes(root: Path, env: str, *, timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    """Live partner front-door probes from the test VM, or an offline notice when Apigee is not deployed.

    The live probes are the catalog's (``tests/partner/scenarios.py``, every scenario with a ``probe``
    number), run by ``tests/partner/probe.py`` on the VM. The Apigee bundle invariants are offline
    tests (``make test-apigee``).
    """
    cmd = ["partner-probes", env]
    platform = testvm.env_rel(env, testvm.APIGEE_PLATFORM_UNIT)
    offline = "the proxy bundles are checked offline by make test-apigee"
    if not (repo.env_dir(root, env) / testvm.APIGEE_PLATFORM_UNIT).is_dir():
        return subprocess.CompletedProcess(
            cmd, 0, f"ℹ️  Apigee platform directory not deployed in {env}: no live probes ({offline}).\n", ""
        )
    if not testvm.apigee_env(root, env):
        notice = f"ℹ️  Apigee is disabled in {env} (no live state in {platform}): no live probes ({offline}).\n"
        return subprocess.CompletedProcess(cmd, 0, notice, "")
    files = {key: root / path for key, path in PARTNER_FILES.items()}
    missing = [str(path) for path in PARTNER_FILES.values() if not (root / path).is_file()]
    if missing:
        raise OpsError(f"{', '.join(missing)} not found in this checkout")
    try:
        vm = testvm.resolve(root, env)
        key = testvm.partner_key(root, env)
    except OpsError as exc:
        return subprocess.CompletedProcess(cmd, 1, "", f"❌ {exc}\n")
    host = f"partner.{vm.domain}"
    header = f"🔎 Running live partner conformance probes against https://{host} ({vm.entry_ip}) from {vm.name}...\n"
    variables = {
        "PARTNER_ENTRY_IP": vm.entry_ip,
        "PARTNER_HOST": host,
        "PARTNER_API_KEY": key,
        "CA_PEM": vm.ca_pem,
        QUOTA_VAR: repo.tool_env().get(QUOTA_VAR) or "0",
    }
    result = testvm.run(vm, PARTNER_REMOTE, vars=variables, files=files, timeout=timeout)
    stdout = header + result.stdout
    if result.returncode == 0:
        stdout += "✅ All live partner front-door conformance probes passed!\n"
    return subprocess.CompletedProcess(cmd, result.returncode, stdout, result.stderr)


def test_partner(
    root: Path,
    env: str,
    *,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Apigee partner front-door conformance probes (``run_partner_probes``), summarised."""
    repo.env_dir(root, env)
    started = time.monotonic()
    result = run_partner_probes(root, env, timeout=timeout)
    parsed = parse_partner_output(result.stdout + "\n" + result.stderr)
    probes = parsed["probes"]
    passed = result.returncode == 0
    summary: dict[str, Any] = {
        "env": env,
        "passed": passed,
        "mode": parsed["mode"],
        "ok": sum(p["ok"] for p in probes),
        "failed": sum(not p["ok"] for p in probes),
        "probes": probes,
        "seconds": round(time.monotonic() - started, 1),
    }
    notices = [line.strip() for line in result.stdout.splitlines() if line.startswith("ℹ️")]
    if notices:
        summary["notice"] = notices[0]
    if not passed:
        text = f"{result.stderr}\n{result.stdout}"
        fatal = [line.strip() for line in _ANSI.sub("", text).splitlines() if line.strip().startswith("❌ ")]
        summary["error"] = repo.short(fatal[-1] if fatal else " | ".join(repo.error_lines(text, 4)), 300)
        if found := repo.hint(text):
            summary["hint"] = found
    return summary


DEFAULT_VERIFY_MESSAGE = "Can you verify Julian Sterling's income?"
DEFAULT_A2A_FOLLOWUP_MESSAGE = (
    "Without me repeating the name, which applicant did I just ask about in this conversation? "
    "Reply with their full name."
)
DEFAULT_VERIFY_AGENTS = ("ai-coe-mortgage-specialist", "cx-mortgage-orchestrator")


async def verify_a2a_context(
    root: Path,
    env: str,
    agent: str = "ai-coe-mortgage-specialist",
    *,
    first_message: str = DEFAULT_VERIFY_MESSAGE,
    followup_message: str = DEFAULT_A2A_FOLLOWUP_MESSAGE,
    expected_terms: tuple[str, ...] = ("julian", "sterling"),
    context_id: str | None = None,
    first_turn: dict[str, Any] | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Sends two A2A messages with the same ``context_id`` (and no ``task_id``) and checks turn 2 recalls turn 1."""
    cid = context_id or f"verify-{uuid.uuid4()}"
    if first_turn is None:
        try:
            first_turn = await query_agent(root, env, agent, first_message, timeout=timeout, session=cid)
        except OpsError as exc:
            first_turn = {"env": env, "agent": agent, "passed": False, "errors": [str(exc)], "seconds": 0.0}
    if not first_turn.get("passed"):
        return {
            "env": env,
            "agent": agent,
            "context_id": cid,
            "passed": False,
            "errors": first_turn.get("errors") or ["first turn failed"],
            "seconds": 0.0,
        }
    try:
        followup = await query_agent(root, env, agent, followup_message, timeout=timeout, session=cid)
    except OpsError as exc:
        followup = {"env": env, "agent": agent, "passed": False, "errors": [str(exc)], "seconds": 0.0}

    answer = str(followup.get("answer") or "")
    check_terms = [t.lower() for t in expected_terms if t.lower() in first_message.lower()]
    recalled = (not check_terms) or any(t in answer.lower() for t in check_terms)
    passed = bool(followup.get("passed")) and bool(answer.strip()) and recalled
    result = {**followup, "context_id": cid, "passed": passed}
    if not passed and followup.get("passed"):
        msg = f"Follow-up A2A message on context_id={cid} did not recall turn-1 context ({', '.join(check_terms)})"
        result["errors"] = [*(result.get("errors") or []), msg]
    return result


def verify_registry_egress(root: Path, env: str) -> dict[str, Any]:
    """Policy-level Agent Gateway egress probe (``esmeralda.ops.registry_egress``), never raising."""
    from esmeralda.ops import registry_egress

    started = time.monotonic()
    try:
        result = registry_egress.check(root, env)
    except (OpsError, ValueError) as exc:
        result = {"env": env, "passed": False, "ok": 0, "failed": 0, "checks": [], "error": repo.short(str(exc), 300)}
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


async def verify(
    root: Path,
    env: str,
    *,
    probe: str | None = None,
    skip_query: bool = False,
    partner: bool = False,
    model_armor: bool = False,
    message: str = DEFAULT_VERIFY_MESSAGE,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """End-to-end verification: gateway conformance probes + remote queries, or partner conformance (--partner)."""
    started = time.monotonic()
    if model_armor:
        from esmeralda.ops.model_armor import verify_model_armor

        return await verify_model_armor(root, env, timeout=timeout)
    if partner:
        pt = test_partner(root, env, timeout=timeout)
        p_steps = [
            {
                "step": f"partner:probe-{p['probe']}" + (f" ({p['label']})" if p.get("label") else ""),
                "passed": bool(p.get("ok")),
                "seconds": 0.0,
                "detail": p.get("detail", ""),
            }
            for p in pt.get("probes") or []
        ]
        if not p_steps:
            p_steps = [
                {
                    "step": "partner",
                    "passed": bool(pt.get("passed")),
                    "seconds": pt.get("seconds", 0.0),
                    "detail": pt.get("error")
                    or pt.get("notice")
                    or f"{pt.get('ok', 0)} ok, {pt.get('failed', 0)} failed",
                }
            ]
        return {
            "env": env,
            "passed": bool(pt.get("passed")),
            "partner": pt,
            "steps": p_steps,
            "seconds": round(time.monotonic() - started, 1),
        }

    gw = test_gateway(root, env, probe=probe, timeout=timeout)
    gw_detail = f"{gw.get('ok', 0)} ok, {gw.get('failed', 0)} failed"
    if not gw.get("passed") and not gw.get("hosts") and gw.get("error"):
        gw_detail = f"{gw_detail} ({gw['error']})"
    steps: list[dict[str, Any]] = [
        {
            "step": "gateway",
            "passed": bool(gw.get("passed")),
            "seconds": gw.get("seconds", 0.0),
            "detail": gw_detail,
        }
    ]
    registry: dict[str, Any] | None = None
    if not probe:
        registry = verify_registry_egress(root, env)
        steps.append(
            {
                "step": "registry-egress",
                "passed": bool(registry.get("passed")),
                "seconds": registry.get("seconds", 0.0),
                "detail": registry.get("error")
                or f"{registry.get('ok', 0)} ok, {registry.get('failed', 0)} failed"
                + "".join(f"; {c['check']}: {c['detail']}" for c in (registry.get("checks") or []) if not c["ok"])[:300],
            }
        )
    queries: dict[str, dict[str, Any]] = {}
    if not skip_query:
        for agent_name in DEFAULT_VERIFY_AGENTS:
            cid = f"verify-{uuid.uuid4()}" if agent_name == "ai-coe-mortgage-specialist" else None
            try:
                qres = await query_agent(root, env, agent_name, message, timeout=timeout, session=cid)
            except OpsError as exc:
                qres = {"env": env, "agent": agent_name, "passed": False, "errors": [str(exc)], "seconds": 0.0}
            queries[agent_name] = qres
            steps.append(
                {
                    "step": agent_name,
                    "passed": bool(qres.get("passed")),
                    "seconds": qres.get("seconds", 0.0),
                    "detail": repo.short(qres.get("answer") or (qres.get("errors") or ["failed"])[0], 120),
                }
            )
            if agent_name == "ai-coe-mortgage-specialist":
                ctx_step = f"{agent_name}:context"
                ctx_res = await verify_a2a_context(
                    root,
                    env,
                    agent_name,
                    first_message=message,
                    context_id=cid,
                    first_turn=qres,
                    timeout=timeout,
                )
                queries[ctx_step] = ctx_res
                steps.append(
                    {
                        "step": ctx_step,
                        "passed": bool(ctx_res.get("passed")),
                        "seconds": ctx_res.get("seconds", 0.0),
                        "detail": repo.short(
                            ctx_res.get("answer") or (ctx_res.get("errors") or ["failed"])[0], 120
                        ),
                    }
                )
    passed = all(s["passed"] for s in steps)
    return {
        "env": env,
        "passed": passed,
        "skip_query": skip_query,
        "probe": probe,
        "steps": steps,
        "gateway": gw,
        **({"registry_egress": registry} if registry is not None else {}),
        "queries": queries,
        "seconds": round(time.monotonic() - started, 1),
    }
