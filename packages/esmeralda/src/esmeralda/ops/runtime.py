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

``test_gateway`` runs ``scripts/test_gateway.sh <env>`` and parses its per-host lines.
"""

from __future__ import annotations

import io
import re
import time
import uuid
from pathlib import Path
from typing import Any

from esmeralda.ops import repo
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
        raise OpsError(f"No engine_id output for {agent} in {env}: deploy it (make deploy-{agent} ENV={env})")
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
    """Per-host results from the output of ``scripts/test_gateway.sh``."""
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


def test_gateway(
    root: Path,
    env: str,
    *,
    probe: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    repo.env_dir(root, env)
    script = root / "scripts" / "test_gateway.sh"
    if not script.is_file():
        raise OpsError(f"{script.relative_to(root)} not found in this checkout")
    started = time.monotonic()
    result = repo.run(["bash", str(script), env], cwd=root, timeout=timeout, what="scripts/test_gateway.sh")
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
    """Per-probe results from the output of ``scripts/test_partner.sh``."""
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


def test_partner(
    root: Path,
    env: str,
    *,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Runs ``scripts/test_partner.sh <env>`` (Apigee partner front-door conformance checks)."""
    repo.env_dir(root, env)
    script = root / "scripts" / "test_partner.sh"
    if not script.is_file():
        raise OpsError(f"{script.relative_to(root)} not found in this checkout")
    started = time.monotonic()
    result = repo.run(["bash", str(script), env], cwd=root, timeout=timeout, what="scripts/test_partner.sh")
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


async def verify(
    root: Path,
    env: str,
    *,
    probe: str | None = None,
    skip_query: bool = False,
    partner: bool = False,
    message: str = DEFAULT_VERIFY_MESSAGE,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """End-to-end verification: gateway conformance probes + remote queries, or partner conformance (--partner)."""
    started = time.monotonic()
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
                    "detail": pt.get("error") or f"{pt.get('ok', 0)} ok, {pt.get('failed', 0)} failed",
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
        "queries": queries,
        "seconds": round(time.monotonic() - started, 1),
    }
