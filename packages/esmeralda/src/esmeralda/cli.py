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

"""``esmeralda`` command line.

Container & agent runtime commands:
* ``esmeralda run -- <command> [args...]``: the container entrypoint. Installs the Agent Gateway
  root CA, then replaces itself with the server command (``exec``), so the server keeps PID 1 and
  receives signals directly.
* ``esmeralda serve [options]``: serves an agent (ADK API server or A2A server) the same way locally
  and in the container. ``--local`` applies ``local_env`` from ``agent.yaml``.
* ``esmeralda query [options] MESSAGE``: queries an agent (ADK or A2A) in-process, on a running
  server (``--url``) or on Agent Runtime (``--engine``), and exits non-zero if the run failed.
* ``esmeralda mcp [--repo PATH]``: a read-only MCP server (stdio) to operate the checkout: status,
  plans, agent queries, gateway probes and agent logs (see ``esmeralda.ops``).

Repository & platform operations (backed by ``esmeralda.ops``):
* ``esmeralda status [--env dev|prd] [--json]``
* ``esmeralda doctor [--env dev|prd] [--json]``
* ``esmeralda units [--env dev|prd] [--layer 0..5] [--json]``
* ``esmeralda plan <unit|--all> [--env dev|prd] [--json]``
* ``esmeralda apply <unit> [--env dev|prd] [--allow-destroy] [--json]``
* ``esmeralda verify [--env dev|prd] [--probe <name>] [--skip-query] [--json]``
* ``esmeralda logs <target> [--env dev|prd] [--minutes 30] [--severity DEFAULT] [--limit 50] [--json]``
* ``esmeralda contracts [--section all|entrypoint|gateway_adapter|agent_card|env_yaml] [--json]``
* ``esmeralda guard [--staged | --diff <range> | --all] [--json]``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from esmeralda import certs

USAGE = (
    "usage: esmeralda {run,serve,query,mcp,status,doctor,units,plan,apply,verify,logs,contracts,guard} ...  "
    "(esmeralda run -- <command> [args...])"
)


def _run(command: list[str]) -> int:
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print(USAGE, file=sys.stderr)
        return 2

    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        installed = certs.install_gateway_ca()
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    if installed:
        print(f"✅ Installed {installed} Agent Gateway root certificate(s) from {certs.ENV_VAR}", flush=True)

    sys.stdout.flush()
    os.execvp(command[0], command)
    return 0  # unreachable: execvp only returns by raising


def _query(args: argparse.Namespace) -> int:
    from esmeralda import query as q
    from esmeralda.config import AgentConfig
    from esmeralda.report import Report

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    try:
        config = AgentConfig.load(args.agent_dir)
        request = q.Query(
            message=args.message,
            user_id=args.user,
            session_id=args.session,
            caller=q.parse_caller(args.caller) if args.caller else None,
            timeout=args.timeout,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    report = Report(verbose=args.verbose)
    try:
        asyncio.run(q.run(config, request, report, url=args.url, engine=args.engine))
    except Exception as exc:  # report transport and agent failures as a failed query
        report.errors.append(f"{type(exc).__name__}: {exc}")
        if args.verbose:
            import traceback

            traceback.print_exc()
    return report.summary(fail_on_tool_error=args.fail_on_tool_error)


def _serve(args: argparse.Namespace) -> int:
    from esmeralda import serve
    from esmeralda.config import AgentConfig

    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = AgentConfig.load(args.agent_dir)
        serve.serve(
            config,
            host=args.host,
            port=args.port,
            local=args.local,
            web=args.web,
            otel_to_cloud=args.otel_to_cloud,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    return 0


def _resolve_root(repo_arg: str | None) -> Path:
    from esmeralda.ops import repo

    return repo.checkout(repo_arg) if repo_arg else repo.repo_root()


def _format_status(data: dict[str, Any]) -> str:
    lines: list[str] = []
    g = data.get("git") or {}
    state_label = f"dirty ({g.get('dirty_files', 0)} file(s))" if g.get("dirty") else "clean"
    wts = g.get("worktrees") or []
    lines.append(
        f"🔀 Git: {g.get('branch', 'unknown')} @ {g.get('head_sha', 'unknown')} "
        f"({state_label}) — {len(wts)} worktree(s)"
    )
    for wt in wts:
        lines.append(f"   • {wt.get('branch', '?')} @ {wt.get('head', '?')}  {wt.get('path', '')}")

    env = data.get("env", "dev")
    backend = data.get("backend", "local")
    deployed = data.get("deployed", 0)
    total = data.get("units", 0)
    local_units = data.get("local_state_units") or []
    lines.append(
        f"📦 Environment '{env}' ({backend} backend): {deployed}/{total} unit(s) deployed "
        f"({len(local_units)} local state file(s))"
    )

    imgs = data.get("images") or {}
    if imgs:
        lines.append(
            f"🐳 Images: repo={imgs.get('image_repository')} tag={imgs.get('container_tag')} "
            f"(HEAD tag: {imgs.get('expected_sha_tag')})"
        )
        for u, dlist in (imgs.get("digests") or {}).items():
            for d in dlist:
                short_d = d.rsplit("/", 1)[-1]
                lines.append(f"   • {u}: {short_d[:64]}")

    if ep := data.get("entry_point"):
        lines.append(
            f"🚪 Entry point: ip={ep.get('ip') or '-'} domain={ep.get('domain') or '-'} "
            f"routing={ep.get('routing')}"
        )
    if gws := data.get("gateways"):
        for gw_name, gw_info in gws.items():
            routes = ", ".join(gw_info.get("routes") or []) or "(no routes)"
            lines.append(f"🌐 Gateway '{gw_name}': {routes}")
    if agents := data.get("agents"):
        for ag_name, ag_info in agents.items():
            lines.append(f"🤖 Agent '{ag_name}': {ag_info.get('engine_id')}")
    if mcps := data.get("mcp_services"):
        for svc_name, uri in mcps.items():
            lines.append(f"🔧 MCP '{svc_name}': {uri}")
    if not_dep := data.get("not_deployed"):
        lines.append(f"⏳ Not deployed: {', '.join(not_dep)}")
    return "\n".join(lines)


def _format_doctor(data: dict[str, Any]) -> str:
    icons = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    lines = [f"🩺 Esmeralda Doctor (env={data.get('env', 'dev')}):"]
    for c in data.get("checks") or []:
        icon = icons.get(c.get("status", ""), "•")
        req = "" if c.get("required", True) else " [optional]"
        lines.append(f"  {icon} {c.get('name', ''):<30} {c.get('detail', '')}{req}")
    verdict = (
        "✅ All required checks passed."
        if data.get("ok")
        else f"❌ {data.get('failed', 1)} required check(s) failed."
    )
    lines.append(verdict)
    return "\n".join(lines)


def _format_units(data: dict[str, Any]) -> str:
    units = data.get("units") or []
    layer_suffix = f" (layer {data['layer']})" if data.get("layer") is not None else ""
    lines = [f"🧱 Units in '{data.get('env', 'dev')}'{layer_suffix} ({data.get('backend', 'local')} backend):"]
    if not units:
        lines.append("  (no matching units)")
        return "\n".join(lines)
    w_unit = max(len(str(u.get("unit", ""))) for u in units)
    lines.append(f"  {'UNIT':<{w_unit}}  LAYER  STATE     ENABLED  DEPS")
    for u in units:
        state_str = "deployed" if u.get("deployed") else ("local" if u.get("local_state") else "none")
        en_str = "yes" if u.get("enabled", True) else "excluded"
        deps = ", ".join(u.get("dependencies") or []) or "-"
        lyr = str(u.get("layer") if u.get("layer") is not None else "-")
        lines.append(f"  {u.get('unit', ''):<{w_unit}}  {lyr:<5}  {state_str:<8}  {en_str:<7}  {deps}")
    return "\n".join(lines)


def _format_single_plan(data: dict[str, Any]) -> str:
    counts = data.get("counts") or {}
    to_add = counts.get("add", 0)
    to_change = counts.get("change", 0)
    to_destroy = counts.get("destroy", 0)
    to_replace = counts.get("replace", 0)
    label = f"{data.get('env', 'dev')}/{data.get('unit', '?')}"
    lines = [
        f"📋 Plan {label}: {to_add} to add, {to_change} to change, {to_destroy} to destroy"
        + (f", {to_replace} to replace" if to_replace else "")
        + f" ({data.get('seconds', 0)}s)"
    ]
    for r in data.get("resources") or []:
        lines.append(f"  {r}")
    if out_changes := data.get("output_changes"):
        lines.append(f"  outputs changed: {', '.join(out_changes)}")
    return "\n".join(lines)


def _format_plan(data: dict[str, Any]) -> str:
    if data.get("all"):
        counts = data.get("counts") or {}
        lines = [
            f"📋 Plan all enabled units in '{data.get('env', 'dev')}': "
            f"{counts.get('add', 0)} to add, {counts.get('change', 0)} to change, "
            f"{counts.get('destroy', 0)} to destroy"
            + (f", {counts.get('replace', 0)} to replace" if counts.get("replace") else "")
        ]
        for u_plan in data.get("units") or []:
            lines.append(_format_single_plan(u_plan))
        return "\n".join(lines)
    return _format_single_plan(data)


def _format_apply(data: dict[str, Any]) -> str:
    lines = [_format_single_plan(data)]
    if data.get("refused"):
        lines.append(f"⛔ {data.get('reason')}")
    elif data.get("applied"):
        lines.append(f"✅ Applied {data.get('env')}/{data.get('unit')} ({data.get('apply_seconds', 0)}s)")
    return "\n".join(lines)


def _format_verify(data: dict[str, Any]) -> str:
    lines = [f"🔎 Verify '{data.get('env', 'dev')}':"]
    for step in data.get("steps") or []:
        icon = "✅" if step.get("passed") else "❌"
        lines.append(f"  {icon} {step.get('step', ''):<28} ({step.get('seconds', 0)}s)  {step.get('detail', '')}")
    verdict = "✅ Verification passed." if data.get("passed") else "❌ Verification failed."
    lines.append(verdict)
    return "\n".join(lines)


def _format_logs(data: dict[str, Any]) -> str:
    lines = [
        f"📜 Logs for '{data.get('target') or data.get('agent')}' in '{data.get('env')}' "
        f"(since={data.get('since')}, severity{data.get('severity')}, returned={data.get('returned', 0)}):"
    ]
    entries = data.get("entries") or []
    if not entries:
        lines.append("  (no log entries)")
    for e in entries:
        cnt = f" (x{e['count']})" if e.get("count", 1) > 1 else ""
        lines.append(f"  [{e.get('time', '?')}] {e.get('severity', 'DEFAULT'):<8}{cnt} {e.get('message', '')}")
    return "\n".join(lines)


def _format_contracts(data: dict[str, Any]) -> str:
    lines = ["📐 Esmeralda Architectural Contracts:"]
    if "section" in data:
        sec = data["section"]
        lines.append(f"\n=== {sec} ===")
        val = data.get(sec)
        lines.append(val if isinstance(val, str) else json.dumps(val, indent=2))
        if "envs" in data:
            lines.append(f"envs: {json.dumps(data['envs'], indent=2)}")
        if "gateway_units" in data:
            lines.append(f"gateway_units: {json.dumps(data['gateway_units'], indent=2)}")
        return "\n".join(lines)

    for key in ("entrypoint", "gateway_adapter", "env_yaml", "agent_card"):
        val = data.get(key)
        if val:
            lines.append(f"\n=== {key} ===")
            lines.append(val if isinstance(val, str) else json.dumps(val, indent=2))
    if envs := data.get("envs"):
        lines.append(f"\n=== envs ===\n{json.dumps(envs, indent=2)}")
    return "\n".join(lines)


def _format_guard(data: dict[str, Any]) -> str:
    if data.get("skipped"):
        return "⚠️  Sensitive-content scan skipped (SKIP_SENSITIVE_SCAN=1)."
    findings = data.get("findings") or []
    if not findings:
        return f"✅ Guard passed ({data.get('mode')} mode, {data.get('scanned_files', 0)} file(s) checked)."
    lines = [
        f"⛔ Sensitive-content check failed ({len(findings)} finding(s) in {data.get('mode')} mode):"
    ]
    for f in findings:
        snippet = f"  [{f['snippet']}]" if f.get("snippet") else ""
        lines.append(f"  • {f.get('where')}: {f.get('rule')}{snippet}")
    return "\n".join(lines)


def _ops_dispatch(args: argparse.Namespace) -> int:
    from esmeralda.ops import contracts, guard, infra, logs, repo, runtime
    from esmeralda.ops.repo import OpsError

    try:
        root = _resolve_root(getattr(args, "repo", None))
        if args.cmd == "status":
            unit_arg = getattr(args, "unit", None)
            data = infra.status(root, args.env, unit_arg) if unit_arg else repo.status(root, args.env)
            print(json.dumps(data, indent=2) if args.json else _format_status(data))
            return 0

        if args.cmd == "doctor":
            data = repo.doctor(root, args.env)
            print(json.dumps(data, indent=2) if args.json else _format_doctor(data))
            return 0 if data.get("ok") else 1

        if args.cmd == "units":
            data = infra.list_units(root, args.env, layer=args.layer)
            print(json.dumps(data, indent=2) if args.json else _format_units(data))
            return 0

        if args.cmd == "plan":
            if not args.all_units and not args.unit:
                raise OpsError("Specify a <unit> or pass --all")
            target = "--all" if args.all_units else args.unit
            data = infra.plan_unit(root, args.env, target, all_units=args.all_units)
            print(json.dumps(data, indent=2) if args.json else _format_plan(data))
            return 0

        if args.cmd == "apply":
            data = infra.apply_unit(root, args.env, args.unit, allow_destroy=args.allow_destroy)
            print(json.dumps(data, indent=2) if args.json else _format_apply(data))
            return int(data.get("exit_code", 2 if data.get("refused") else 0))

        if args.cmd == "verify":
            data = asyncio.run(
                runtime.verify(root, args.env, probe=args.probe, skip_query=args.skip_query)
            )
            print(json.dumps(data, indent=2) if args.json else _format_verify(data))
            return 0 if data.get("passed") else 1

        if args.cmd == "logs":
            data = logs.agent_logs(
                root,
                args.env,
                args.target,
                minutes=args.minutes,
                severity=args.severity,
                limit=args.limit,
            )
            print(json.dumps(data, indent=2) if args.json else _format_logs(data))
            return 0

        if args.cmd == "contracts":
            data = contracts.describe_contracts(root, section_name=args.section)
            print(json.dumps(data, indent=2) if args.json else _format_contracts(data))
            return 0

        if args.cmd == "guard":
            mode = "all" if args.all else ("diff" if args.diff else "staged")
            data = guard.run_guard(
                root,
                mode=mode,
                diff_range=args.diff,
                message_file=args.message_file,
            )
            print(json.dumps(data, indent=2) if args.json else _format_guard(data))
            return 0 if data.get("ok") else 1

    except OpsError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"❌ {exc}", file=sys.stderr)
        return 2
    return 0


def _add_common_ops_flags(subparser: argparse.ArgumentParser, *, with_env: bool = True) -> None:
    if with_env:
        subparser.add_argument("--env", default="dev", help="environment under infrastructure/live (default: dev)")
    subparser.add_argument("--repo", help="repository root (default: $ESMERALDA_REPO or git checkout root)")
    subparser.add_argument("--json", action="store_true", help="output machine-readable JSON")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="esmeralda", description="Esmeralda agent runtime & platform operations CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="container entrypoint: install the gateway CA, then exec the command")
    run.add_argument("command", nargs=argparse.REMAINDER)

    serve = sub.add_parser("serve", help="serve an agent: ADK API server or A2A server, from agent.yaml")
    serve.add_argument("--agent-dir", default=".", help="agent directory, with agent.yaml (default: .)")
    serve.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    serve.add_argument("--port", type=int, default=None, help="port (default: $PORT or 8080)")
    serve.add_argument("--local", action="store_true", help="apply local_env from agent.yaml (workstation runs)")
    serve.add_argument("--web", action="store_true", help="ADK dev UI instead of the API server (ADK agents)")
    serve.add_argument("--otel-to-cloud", action="store_true", help="export traces and logs to Google Cloud")

    query = sub.add_parser("query", help="query an ADK or A2A agent (in-process, --url or --engine)")
    query.add_argument("message", help="the user message")
    query.add_argument("--agent-dir", default=".", help="agent directory, with agent.yaml (default: .)")
    target = query.add_mutually_exclusive_group()
    target.add_argument("--url", help="a running server, e.g. http://localhost:8080")
    target.add_argument("--engine", help="projects/P/locations/L/reasoningEngines/ID on Agent Runtime")
    query.add_argument("--user", default="esmeralda-cli", help="user id (default: esmeralda-cli)")
    query.add_argument("--session", help="session id (ADK) or context id (A2A) to continue")
    query.add_argument("--caller", help="caller context as project/agent (default: <project>/esmeralda_cli)")
    query.add_argument("--timeout", type=float, default=300.0, help="HTTP timeout in seconds (default: 300)")
    query.add_argument("--fail-on-tool-error", action="store_true", help="exit non-zero if a tool call errored")
    query.add_argument("--verbose", "-v", action="store_true", help="print raw events")

    mcp = sub.add_parser("mcp", help="read-only MCP server (stdio) to operate this checkout")
    mcp.add_argument("--repo", help="repository root (default: $ESMERALDA_REPO, else the checkout containing cwd)")

    status = sub.add_parser(
        "status",
        help="show git branch/SHA, active worktrees, local state units, image tags/digests, and deployed outputs",
    )
    status.add_argument("--unit", help="optional unit path for its full (non-sensitive) outputs")
    _add_common_ops_flags(status, with_env=True)

    doctor = sub.add_parser(
        "doctor",
        help="preflight check: required tools, billing env var, ADC token, uv.lock, and local state readability",
    )
    _add_common_ops_flags(doctor, with_env=True)

    units = sub.add_parser(
        "units",
        help="list Terragrunt units, layer, dependencies, enabled/excluded status, and local state presence",
    )
    units.add_argument("--layer", type=int, choices=range(0, 6), help="filter by layer number (0..5)")
    _add_common_ops_flags(units, with_env=True)

    plan = sub.add_parser(
        "plan",
        help="run read-only terragrunt plan on a unit (or --all) and print a compact summary",
    )
    plan.add_argument(
        "unit",
        nargs="?",
        help="unit path or short name (e.g. layer-5-workloads/entrypoint or entrypoint)",
    )
    plan.add_argument("--all", action="store_true", dest="all_units", help="plan all enabled units in the environment")
    _add_common_ops_flags(plan, with_env=True)

    apply_p = sub.add_parser(
        "apply",
        help="plan a unit to a planfile, enforce the destroy guard, and apply the saved planfile",
    )
    apply_p.add_argument("unit", help="unit path or short name (e.g. layer-5-workloads/entrypoint or entrypoint)")
    apply_p.add_argument(
        "--allow-destroy",
        action="store_true",
        help="permit plans that destroy or replace resources (otherwise exits 2)",
    )
    _add_common_ops_flags(apply_p, with_env=True)

    verify = sub.add_parser(
        "verify",
        help="run gateway conformance probes and remote specialist + orchestrator queries",
    )
    verify.add_argument("--probe", help="filter gateway conformance check to a specific host or probe kind")
    verify.add_argument(
        "--skip-query",
        action="store_true",
        help="run gateway probes only, skipping remote agent queries",
    )
    _add_common_ops_flags(verify, with_env=True)

    logs_p = sub.add_parser(
        "logs",
        help="fetch recent Cloud Logging entries for an agent or MCP service (tracebacks collapsed, deduplicated)",
    )
    logs_p.add_argument("target", help="agent or MCP service name (e.g. cx-mortgage-orchestrator, legacy-dms)")
    logs_p.add_argument("--minutes", type=int, default=30, help="look-back window in minutes (default: 30)")
    logs_p.add_argument("--severity", default="DEFAULT", help="minimum log severity (default: DEFAULT)")
    logs_p.add_argument("--limit", type=int, default=50, help="maximum distinct entries (default: 50)")
    _add_common_ops_flags(logs_p, with_env=True)

    contracts_p = sub.add_parser(
        "contracts",
        help="print architectural contracts (entrypoint, gateway_adapter, agent_card, env_yaml)",
    )
    contracts_p.add_argument(
        "--section",
        default="all",
        choices=["all", "entrypoint", "gateway_adapter", "agent_card", "env_yaml"],
        help="contract section to display (default: all)",
    )
    _add_common_ops_flags(contracts_p, with_env=False)

    guard_p = sub.add_parser(
        "guard",
        help="scan staged changes, a diff range, or all tracked files for secrets and forbidden internal strings",
    )
    guard_mode = guard_p.add_mutually_exclusive_group()
    guard_mode.add_argument("--staged", action="store_true", help="scan staged git changes (default)")
    guard_mode.add_argument("--diff", metavar="RANGE", help="scan a git diff range (e.g. origin/main...HEAD)")
    guard_mode.add_argument("--all", action="store_true", help="scan all tracked files in the repository")
    guard_mode.add_argument("--message-file", help="scan a commit message file")
    _add_common_ops_flags(guard_p, with_env=False)

    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = sys.argv[1:] if argv is None else argv
    try:
        args = _parser().parse_args(args_list)
    except SystemExit as exc:  # argparse exits on usage errors and --help
        return int(exc.code or 0)
    if args.cmd == "run":
        return _run(args.command)
    if args.cmd == "serve":
        return _serve(args)
    if args.cmd == "query":
        return _query(args)
    if args.cmd == "mcp":
        from esmeralda.ops import server

        return server.main(args.repo)
    return _ops_dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
