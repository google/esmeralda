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
  ``--local-mcp`` starts the agent's local MCP servers for an in-process run (``esmeralda.local_mcp``).
* ``esmeralda mcp [--repo PATH]``: a read-only MCP server (stdio) to operate the checkout: status,
  plans, agent queries, gateway probes and agent logs (see ``esmeralda.ops``).

Repository & platform operations (backed by ``esmeralda.ops``):
* ``esmeralda status [--env dev|prd] [--unit <unit> [--output <name>]] [--json]``
* ``esmeralda doctor [--env dev|prd] [--json]``
* ``esmeralda units [--env dev|prd] [--layer 0..5] [--json]``
* ``esmeralda plan <unit|--all> [--env dev|prd] [--json]``
* ``esmeralda apply <unit> [--env dev|prd] [--allow-destroy] [--json]``
* ``esmeralda deploy <unit>... | --layer N | --all [--env dev|prd] [--allow-destroy] [--dry-run] [--json]``
* ``esmeralda destroy --env dev --confirm dev [--dry-run] [--json]``
* ``esmeralda build <agents|services|images|package|<image>> [--tag T] [--skip-tests] [--dry-run] [--verify]``
* ``esmeralda release status|promote [--version vX.Y.Z --source dev-<sha> [--verified DATE] [--dry-run]] [--json]``
* ``esmeralda verify [--env dev|prd] [--probe <name>] [--skip-query] [--partner] [--model-armor] [--json]``
* ``esmeralda logs <target> [--env dev|prd] [--minutes 30] [--severity DEFAULT] [--limit 50] [--json]``
* ``esmeralda contracts [--section all|entrypoint|gateway_adapter|agent_card|env_yaml] [--json]``
* ``esmeralda guard [--staged | --diff <range> | --all] [--json]``
* ``esmeralda catalog resolve [--env <env>|all] [--format json|hcl|terraform] [--write | --check]``
* ``esmeralda catalog show [--env <env>] [--target <name>] [--caller <name>] [--provenance] [--inventory] [--json]``
* ``esmeralda catalog sources [--env <env>] [--json]``
* ``esmeralda catalog fetch --source <name>|all [--env <env>] [--dry-run] [--json]``
* ``esmeralda catalog diff [--env <env>] [--live] [--json]``
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
    "usage: esmeralda {run,serve,query,mcp,status,doctor,units,plan,apply,deploy,destroy,build,release,verify,logs,"
    "contracts,guard,catalog} ...  "
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
        user_token = getattr(args, "user_token", None) or os.environ.get("ESMERALDA_USER_TOKEN") or None
        request = q.Query(
            message=args.message,
            user_id=args.user,
            session_id=args.session,
            caller=q.parse_caller(args.caller) if args.caller else None,
            user_token=user_token,
            timeout=args.timeout,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    report = Report(verbose=args.verbose)
    if getattr(args, "local_mcp", False):
        from esmeralda import local_mcp

        try:
            with local_mcp.sigterm_as_interrupt():
                servers = local_mcp.servers_for(config.local_env)
                if not servers:
                    print("ℹ️  agent.yaml local_env points at no local MCP server: nothing to start", file=sys.stderr)
                with local_mcp.running(local_mcp.find_root(config.directory), servers):
                    return _run_query(args, config, request, report)
        except local_mcp.LocalMcpError as exc:
            print(f"❌ {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Interrupted.", file=sys.stderr)
            return 130
    return _run_query(args, config, request, report)


def _run_query(args: argparse.Namespace, config: Any, request: Any, report: Any) -> int:
    from esmeralda import query as q

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


_STEP_ICONS = {
    "applied": "✅", "planned": "📋", "built": "✅", "passed": "✅", "destroyed": "🧹", "dry-run": "📝",
    "refused": "⛔", "failed": "❌", "skipped": "⏭️", "pending": "⏸️",
}


def _format_deploy(data: dict[str, Any]) -> str:
    action = "Destroy" if data.get("destroy") else "Deploy"
    mode = " (dry run: plans only, nothing applied)" if data.get("dry_run") else ""
    lines = [f"🚀 {action} '{data.get('env')}'{mode}:"]
    for step in data.get("steps") or []:
        icon = _STEP_ICONS.get(step.get("status", ""), "•")
        label = step["step"] if step.get("kind") == "unit" else f"{step.get('kind')} {step['step']}"
        detail = ""
        if "counts" in step or step.get("status") in ("planned", "applied", "refused"):
            c = step.get("counts") or {}
            detail = f"+{c.get('add', 0)} ~{c.get('change', 0)} -{c.get('destroy', 0)}"
            detail += f" ±{c['replace']}" if c.get("replace") else ""
        if step.get("would_refuse"):
            detail += "  ⚠️  destroy/replace: apply would refuse without --allow-destroy"
        if step.get("status") == "refused":
            detail += f"  {step.get('reason', '')}"
        if step.get("error"):
            detail += f"  {step['error']}"
        if step.get("reason") and step.get("status") == "skipped":
            detail += step["reason"]
        seconds = f" ({step['seconds']}s)" if step.get("seconds") is not None else ""
        lines.append(f"  {icon} {step.get('status', ''):<9} {label}{seconds}  {detail}".rstrip())
        for command in step.get("commands") or []:
            lines.append(f"      $ {command}")
        for image in step.get("images") or []:
            for command in image.get("commands") or []:
                lines.append(f"      $ {command}")
            if image.get("status") == "failed":
                lines.append(f"      ❌ {image['image']}: see {image.get('log')}")
        for resource in (step.get("resources") or []) if step.get("status") in ("planned", "refused") else []:
            lines.append(f"      {resource}")
    done = sum(1 for s in data.get("steps") or [] if s.get("status") not in ("pending", "skipped"))
    total = len(data.get("steps") or [])
    if data.get("ok"):
        lines.append(f"✅ {action} {'plan ' if data.get('dry_run') else ''}finished: {done}/{total} step(s).")
    else:
        lines.append(f"❌ {action} stopped at step {done}/{total}; later steps did not run.")
    return "\n".join(lines)


def _format_build(data: dict[str, Any]) -> str:
    if data.get("target") == "package":
        if data.get("dry_run"):
            return "\n".join(f"$ {c}" for c in data.get("commands") or [])
        lines = [f"📦 {name}" for name in data.get("files") or []]
        lines += [f"⛔ {p}" for p in data.get("problems") or []]
        if data.get("ok"):
            lines.append(f"✅ Verified wheel contents: {data.get('wheel')}")
        return "\n".join(lines)
    lines = [
        f"🏗️  Build {data.get('target')} (:{data.get('tag')}, :{data.get('sha_tag')})"
        + (" (dry run)" if data.get("dry_run") else "")
    ]
    if data.get("tests") == "failed":
        lines.append("❌ Tests failed: no image was built.")
    for image in data.get("images") or []:
        icon = _STEP_ICONS.get(image.get("status", ""), "•")
        timing = f" ({image['seconds']}s, log {image['log']})" if "log" in image else ""
        lines.append(f"  {icon} {image['image']}{timing}")
        for command in image.get("commands") or []:
            lines.append(f"      $ {command}")
        if image.get("status") == "failed":
            lines.append(f"      {image.get('error') or image.get('failed_command', '')}")
            lines.extend(f"      | {line}" for line in image.get("tail") or [])
    if not data.get("dry_run"):
        lines.append("✅ All images built and pushed!" if data.get("ok") else "❌ Build failed.")
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


def _format_catalog_show(data: dict[str, Any]) -> str:
    policy = data.get("policy") or {}
    lines = [
        f"📇 Catalog '{data.get('env')}' (unmapped_caller={policy.get('unmapped_caller', '-')}, "
        f"declared_mismatch={policy.get('declared_mismatch', '-')}):"
    ]
    for t in data.get("targets") or []:
        flags = " [example]" if t.get("example") else ""
        flags += "" if t.get("compiled") or t.get("registry") else " [not compiled]"
        ingress = f" ingress={t['ingress_backend']}" if t.get("ingress_backend") else ""
        registry = f" registry={t['registry']}" if t.get("registry") and not t.get("compiled") else ""
        adopted = f" adopted from {t['adopted_from']}" if t.get("adopted_from") else ""
        lines.append(
            f"🎯 {t['target']} ({t['kind']}, owner {t['owner_team']}) {t.get('deploy')} "
            f"gateway={t.get('gateway') or '-'}{ingress}{registry}{adopted}{flags}"
        )
        if t.get("provenance"):
            lines += _format_provenance(t["provenance"])
        if not t.get("callers"):
            lines.append("   (no callers)")
        for c in t.get("callers") or []:
            pays = c.get("pays") or {}
            extra = "".join(f" {k}={c[k]}" for k in ("product", "tools", "quota") if c.get(k))
            enforced = "" if c.get("enforced") or not t.get("compiled") else " [not enforced yet]"
            lines.append(
                f"   • {c['caller']:<28} {c['identity']:<16} via {c.get('gateway') or '-':<13} "
                f"pays: {pays.get('team')} {', '.join(pays.get('cost_centers') or [])}{extra}{enforced}"
            )
    return "\n".join(lines)


def _format_provenance(prov: dict[str, Any]) -> list[str]:
    lines = []
    entry = prov.get("entry") or {}
    if entry:
        lines.append(f"   ↳ from {entry.get('source')} ({entry.get('origin')})")
    for path, record in (prov.get("fields") or {}).items():
        shadowed = record.get("shadowed") or []
        losers = f"; shadows {', '.join(s['source'] for s in shadowed)}" if shadowed else ""
        where = f"{record.get('origin')}, {record.get('strategy', '-')}{losers}"
        lines.append(f"     {path}: {record.get('source')} ({where})")
    return lines


def _format_catalog_inventory(data: dict[str, Any]) -> str:
    rows = data.get("inventory") or []
    lines = [f"📦 Discovered inventory '{data.get('env')}' ({len(rows)} entr{'y' if len(rows) == 1 else 'ies'}; "
             "never compiled, adopt with a deploy.registry pin):"]
    if not rows:
        lines.append("   (none)")
    for r in rows:
        extra = "".join(f" {k}={r[k]}" for k in ("owner_team", "runtime", "endpoint") if r.get(k))
        lines.append(f"🔎 {r['name']} ({r['kind']}) id={r.get('id') or '-'} from {r.get('source')}{extra}")
    return "\n".join(lines)


def _format_catalog_sources(data: dict[str, Any]) -> str:
    lines = [f"🧩 Catalog sources '{data.get('env')}' (merge order):"]
    for s in data.get("sources") or []:
        state = "enabled" if s.get("enabled") else "disabled"
        lines.append(f"• {s['name']} ({s['type']}, {state})")
        if s.get("error"):
            lines.append(f"   ❌ {s['error']}")
        if "paths" in s:
            lines.append(f"   paths: {', '.join(s['paths'])}")
        if s.get("snapshot"):
            status = s.get("snapshot_status")
            detail = {
                "ok": f"{s.get('entries')} entries, sha256 {str(s.get('sha256'))[:12]}",
                "missing": f"missing: run `{s.get('fetch')}`",
                "invalid": "invalid: " + "; ".join(s.get("problems") or []),
            }.get(status, status or "")
            lines.append(f"   snapshot: {s['snapshot']} ({detail}){' [optional]' if s.get('optional') else ''}")
        caps = s.get("capabilities")
        if isinstance(caps, str):
            lines.append(f"   may contribute: {caps}")
        elif caps is not None:
            shown = "; ".join(f"{sec}: {', '.join(paths)}" for sec, paths in caps.items()) or "nothing"
            lines.append(f"   may contribute: {shown}")
    return "\n".join(lines)


def _format_catalog_fetch(data: dict[str, Any]) -> str:
    rows = data.get("sources") or []
    mode = " (dry run, nothing written)" if data.get("dry_run") else ""
    if not rows:
        return f"ℹ️  No enabled snapshot source to fetch for '{data.get('env')}'."
    lines = [f"📥 Catalog fetch '{data.get('env')}'{mode}:"]
    for r in rows:
        verb = r["status"] if r.get("written") or r["status"] == "unchanged" else f"would be {r['status']}"
        lines.append(f"• {r['name']}: {r['snapshot']} {verb} ({r['entries']} entries, sha256 {r['sha256'][:12]})")
        for change in ("added", "removed", "changed"):
            if r.get(change):
                lines.append(f"   {change}: {', '.join(r[change])}")
    return "\n".join(lines)


def _format_catalog_diff(data: dict[str, Any]) -> str:
    findings = data.get("findings") or []
    scope = "live vs snapshot, snapshot vs files" if data.get("live") else "snapshot vs files"
    if not findings:
        return f"✅ No catalog drift for '{data.get('env')}' ({scope})."
    lines = [f"⚠️  {len(findings)} catalog finding(s) for '{data.get('env')}' ({scope}):"]
    for f in findings:
        lines.append(f"• [{f['kind']}] {f['subject']} ({f.get('source') or '-'}): {f['detail']}")
    return "\n".join(lines)


def _catalog_error(args: argparse.Namespace, exc: Exception, code: int) -> int:
    problems = getattr(exc, "problems", None) or [str(exc)]
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "errors": problems}, indent=2))
    else:
        print(f"❌ Catalog error ({len(problems)}):", file=sys.stderr)
        for p in problems:
            print(f"  • {p}", file=sys.stderr)
    return code


def _catalog_sources_cmd(args: argparse.Namespace, root: Path) -> int:
    """``catalog sources | fetch | diff``: exit 1 on an error, 2 when a live source is unreachable,
    3 when ``diff`` has findings."""
    from esmeralda.catalog import commands

    try:
        if args.catalog_cmd == "sources":
            data = commands.describe_sources(root, args.env)
            print(json.dumps(data, indent=2) if args.json else _format_catalog_sources(data))
            return 0
        if args.catalog_cmd == "fetch":
            data = commands.fetch_sources(root, args.env, args.source, dry_run=args.dry_run)
            print(json.dumps(data, indent=2) if args.json else _format_catalog_fetch(data))
            return 0
        data = commands.diff_catalog(root, args.env, live=args.live)
        print(json.dumps(data, indent=2) if args.json else _format_catalog_diff(data))
        return 3 if data["findings"] else 0
    except commands.SourceUnreachable as exc:
        return _catalog_error(args, exc, 2)
    except commands.CatalogError as exc:
        return _catalog_error(args, exc, 1)


def _print_warnings(resolved: Any) -> None:
    for warning in getattr(resolved, "warnings", None) or []:
        print(f"⚠️  {warning}", file=sys.stderr)


def _catalog(args: argparse.Namespace) -> int:
    from esmeralda import catalog
    from esmeralda.ops import repo
    from esmeralda.ops.repo import OpsError

    try:
        root = _resolve_root(getattr(args, "repo", None))
        if args.catalog_cmd in ("sources", "fetch", "diff"):
            if args.env == "all":
                raise catalog.CatalogError(f"catalog {args.catalog_cmd} needs a single --env")
            return _catalog_sources_cmd(args, root)
        envs = catalog.resolver.environments(root) if args.env == "all" else [args.env]
        if args.catalog_cmd == "resolve":
            if args.check or args.write:
                code = 0
                for env in envs:
                    if args.write:
                        path = catalog.write_resolved(root, env)
                        print(f"✅ Wrote {path.relative_to(root).as_posix()}")
                    else:
                        ok, message = catalog.check_resolved(root, env)
                        print(("✅ " if ok else "❌ ") + message, file=sys.stdout if ok else sys.stderr)
                        code = code or (0 if ok else 1)
                return code
            if len(envs) != 1:
                raise catalog.CatalogError("--env all needs --write or --check")
            resolved = catalog.resolve(root, envs[0])
            _print_warnings(resolved)
            if args.format == "hcl":
                print(catalog.render_hcl(resolved), end="")
            elif args.format == "terraform":
                print(catalog.render_resolved_file(resolved), end="")
            else:
                print(json.dumps(resolved.document, indent=2))
            return 0

        if args.catalog_cmd == "show":
            if len(envs) != 1:
                raise catalog.CatalogError("catalog show needs a single --env")
            resolved = catalog.resolve(root, envs[0])
            _print_warnings(resolved)
            if args.inventory:
                data = {"env": resolved.env, "inventory": catalog.resolver.inventory_rows(resolved)}
                if args.provenance:
                    for row in data["inventory"]:
                        row["provenance"] = catalog.resolver.entry_provenance(resolved, "inventory", row["name"])
                print(json.dumps(data, indent=2) if args.json else _format_catalog_inventory(data))
                return 0
            try:
                ingress = repo.gateways_block(root, envs[0])
            except (OpsError, OSError):
                ingress = {}
            data = catalog.show(resolved, target=args.target, caller=args.caller, ingress=ingress,
                                provenance=args.provenance)
            print(json.dumps(data, indent=2) if args.json else _format_catalog_show(data))
            return 0
    except (catalog.CatalogError, OpsError, NotImplementedError) as exc:
        return _catalog_error(args, exc, 1 if args.catalog_cmd in ("sources", "fetch", "diff") else 2)
    return 0


def _ops_dispatch(args: argparse.Namespace) -> int:
    from esmeralda.ops import build, contracts, deploy, guard, infra, logs, release, repo, runtime
    from esmeralda.ops.repo import OpsError

    try:
        root = _resolve_root(getattr(args, "repo", None))
        if args.cmd == "status":
            unit_arg = getattr(args, "unit", None)
            if getattr(args, "output", None):
                if not unit_arg:
                    raise OpsError("--output needs --unit <unit>")
                resolved = infra.resolve_unit(root, args.env, unit_arg)
                value = repo.unit_state(root, args.env, resolved).outputs.get(args.output)
                if value is None:
                    print(f"❌ No output '{args.output}' for {args.env}/{resolved}", file=sys.stderr)
                    return 1
                print(value if isinstance(value, str) else json.dumps(value))
                return 0
            data = infra.status(root, args.env, unit_arg) if unit_arg else repo.status(root, args.env)
            print(json.dumps(data, indent=2) if args.json else _format_status(data))
            return 0

        if args.cmd == "deploy":
            steps = deploy.plan_steps(
                root,
                args.env,
                layers=args.layer,
                units=args.units,
                all_layers=args.all_layers,
                skip_build=args.skip_build,
                skip_verify=args.skip_verify_images,
            )
            options = deploy.Options(
                allow_destroy=args.allow_destroy,
                timeout=args.timeout,
                dry_run=args.dry_run,
                cicd_dir=args.cicd_dir,
                skip_tests=args.skip_tests,
                tag=args.tag,
            )
            data = deploy.deploy(root, args.env, steps, options)
            print(json.dumps(data, indent=2) if args.json else _format_deploy(data))
            return int(data["exit_code"])

        if args.cmd == "destroy":
            data = deploy.destroy(root, args.env, confirm=args.confirm, dry_run=args.dry_run, timeout=args.timeout)
            print(json.dumps(data, indent=2) if args.json else _format_deploy(data))
            return int(data["exit_code"])

        if args.cmd == "build":
            if not args.target and not args.verify:
                raise OpsError(
                    f"Specify what to build ({', '.join(build.TARGETS)} or an image: {', '.join(build.image_names())}) "
                    "or pass --verify"
                )
            code = 0
            if args.target == "package":
                data = build.build_package(root, dry_run=args.dry_run)
                print(json.dumps(data, indent=2) if args.json else _format_build(data))
                code = int(data["exit_code"])
            elif args.target:
                data = build.build_images(
                    root,
                    args.target,
                    tag=args.tag,
                    cicd_dir=args.cicd_dir,
                    skip_tests=args.skip_tests,
                    dry_run=args.dry_run,
                    timeout=args.timeout,
                    jobs=args.jobs,
                )
                print(json.dumps(data, indent=2) if args.json else _format_build(data))
                code = int(data["exit_code"])
            if args.verify and code == 0:
                if args.dry_run:
                    print(f"$ {' '.join(release.verify_images_cmd(args.env))}")
                else:
                    data = release.verify_images(
                        root, args.env, cicd_dir=args.cicd_dir, echo=None if args.json else print
                    )
                    if args.json:
                        print(json.dumps(data, indent=2))
                    code = int(data["exit_code"])
            return code

        if args.cmd == "release":
            if args.release_cmd == "promote":
                data = release.promote(
                    root,
                    version=args.version,
                    source=args.source,
                    verified=args.verified,
                    env=args.env,
                    dry_run=args.dry_run,
                    echo=(lambda line: print(line, file=sys.stderr)) if args.json else print,
                )
                if args.json:
                    print(json.dumps(data, indent=2))
                return int(data["exit_code"])
            data = release.status(root, args.env)
            print(json.dumps(data, indent=2) if args.json else release.format_status(data))
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
            data = infra.apply_unit(
                root, args.env, args.unit, allow_destroy=args.allow_destroy, timeout=args.timeout
            )
            print(json.dumps(data, indent=2) if args.json else _format_apply(data))
            return int(data.get("exit_code", 2 if data.get("refused") else 0))

        if args.cmd == "verify":
            data = asyncio.run(
                runtime.verify(
                    root,
                    args.env,
                    probe=args.probe,
                    skip_query=args.skip_query,
                    partner=getattr(args, "partner", False),
                    model_armor=getattr(args, "model_armor", False),
                )
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


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of seconds: {value!r}") from None
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be a positive number of seconds")
    return seconds


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
    target.add_argument(
        "--local-mcp",
        action="store_true",
        help="in-process run: start the agent's local MCP servers (agent.yaml local_env ports, 127.0.0.1), "
        "query, then stop them",
    )
    query.add_argument("--user", default="esmeralda-cli", help="user id (default: esmeralda-cli)")
    query.add_argument("--session", help="session id (ADK) or context id (A2A) to continue")
    query.add_argument("--caller", help="caller context as project/agent (default: <project>/esmeralda_cli)")
    query.add_argument(
        "--user-token",
        default=os.environ.get("ESMERALDA_USER_TOKEN"),
        help="end-user OAuth token forwarded as temp:user_auth_token / A2A metadata (default: $ESMERALDA_USER_TOKEN)",
    )
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
    status.add_argument("--output", metavar="NAME", help="with --unit: print just this output's raw value")
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
    apply_p.add_argument(
        "--timeout",
        type=_positive_seconds,
        default=900.0,
        help="seconds allowed for the plan and for the apply (default: 900); raise it for long provisioners",
    )
    _add_common_ops_flags(apply_p, with_env=True)

    deploy_p = sub.add_parser(
        "deploy",
        help="apply units, whole layers or the whole env in dependency order (destroy guard per unit, "
        "stops at the first failure)",
    )
    deploy_p.add_argument(
        "units",
        nargs="*",
        help="units (path or short name; shared/layer-0-cicd for layer 0), applied in dependency order",
    )
    deploy_p.add_argument(
        "--layer", type=int, action="append", choices=range(0, 6), metavar="N",
        help="deploy layer N (0..5; repeatable). Layer 5 = every enabled workload unit, iap-egress last, then the "
        "running-image check",
    )
    deploy_p.add_argument(
        "--all", action="store_true", dest="all_layers",
        help="layer 0 -> 1-3 -> 4 -> image builds -> layer 5 (as make deploy-all)",
    )
    deploy_p.add_argument("--allow-destroy", action="store_true", help="permit destroy/replace plans in every unit")
    deploy_p.add_argument(
        "--timeout", type=_positive_seconds, default=1800.0,
        help="seconds allowed for each unit's plan and apply (default: 1800)",
    )
    deploy_p.add_argument("--dry-run", action="store_true", help="plan each unit (read-only) and print build commands")
    deploy_p.add_argument("--skip-build", action="store_true", help="with --all: don't build images")
    deploy_p.add_argument(
        "--skip-tests", action="store_true", help="with --all: don't run the tests before agent builds"
    )
    deploy_p.add_argument(
        "--skip-verify-images", action="store_true", help="don't run the running-image check after layer 5"
    )
    deploy_p.add_argument("--tag", help="image tag for the --all builds (default: $BUILD_TAG or dev-latest)")
    deploy_p.add_argument(
        "--cicd-dir", help="layer-0 unit directory (default: this checkout, or the main checkout's for a worktree)"
    )
    _add_common_ops_flags(deploy_p, with_env=True)

    destroy_p = sub.add_parser(
        "destroy", help="destroy an env's layers 5 -> 1, unit by unit (never prd, never the shared layer 0)"
    )
    destroy_p.add_argument("--confirm", metavar="ENV", help="the environment name again, to confirm")
    destroy_p.add_argument("--dry-run", action="store_true", help="list what would be destroyed, in order")
    destroy_p.add_argument(
        "--timeout", type=_positive_seconds, default=1800.0, help="seconds allowed for each unit (default: 1800)"
    )
    _add_common_ops_flags(destroy_p, with_env=True)

    build_p = sub.add_parser(
        "build",
        help="build images with Cloud Build in the shared CI/CD project (agents, services, images, one image), "
        "the esmeralda-platform package, or --verify running images",
    )
    build_p.add_argument(
        "target", nargs="?",
        help="agents | services | images | package | an image (e.g. legacy-dms, cx-mortgage-orchestrator)",
    )
    build_p.add_argument("--tag", help="mutable tag (default: $BUILD_TAG or dev-latest); :dev-<gitsha> is always added")
    build_p.add_argument("--skip-tests", action="store_true", help="don't run the tests before agent builds")
    build_p.add_argument("--dry-run", action="store_true", help="print the commands instead of running them")
    build_p.add_argument(
        "--verify", action="store_true",
        help="check every layer-5 workload runs the expected digest (release/<env>.yaml, else its tag) for --env",
    )
    build_p.add_argument("--jobs", type=int, help="concurrent builds per batch (default: all of the batch)")
    build_p.add_argument(
        "--timeout",
        type=_positive_seconds,
        default=3600.0,
        help="seconds allowed for each build command (default: 3600)",
    )
    build_p.add_argument(
        "--cicd-dir", help="layer-0 unit directory (default: this checkout, or the main checkout's for a worktree)"
    )
    _add_common_ops_flags(build_p, with_env=True)

    release_p = sub.add_parser("release", help="promote a verified dev build by digest, or show the release manifest")
    release_sub = release_p.add_subparsers(dest="release_cmd", required=True)
    rel_status = release_sub.add_parser("status", help="show release/<env>.yaml and the shared repositories")
    rel_status.add_argument("--env", default="prd", help="release environment (default: prd)")
    rel_status.add_argument("--repo", help="repository root (default: $ESMERALDA_REPO or git checkout root)")
    rel_status.add_argument("--json", action="store_true", help="output machine-readable JSON")
    rel_promote = release_sub.add_parser(
        "promote", help="copy every dev-<sha> image by digest to the release repository and write release/<env>.yaml"
    )
    rel_promote.add_argument("--version", required=True, help="release version, vX.Y.Z")
    rel_promote.add_argument("--source", required=True, help="immutable dev build tag, dev-<gitsha>")
    rel_promote.add_argument("--verified", help="date (YYYY-MM-DD) verify passed in dev on that build (default: today)")
    rel_promote.add_argument("--env", default="prd", help="release environment (default: prd)")
    rel_promote.add_argument(
        "--dry-run", action="store_true",
        help="resolve and check every digest and show what would change; copy nothing, write nothing",
    )
    rel_promote.add_argument("--repo", help="repository root (default: $ESMERALDA_REPO or git checkout root)")
    rel_promote.add_argument("--json", action="store_true", help="output machine-readable JSON (progress on stderr)")

    verify = sub.add_parser(
        "verify",
        help="run gateway conformance probes and remote specialist + orchestrator queries (or --partner checks)",
    )
    verify.add_argument("--probe", help="filter gateway conformance check to a specific host or probe kind")
    verify.add_argument(
        "--skip-query",
        action="store_true",
        help="run gateway probes only, skipping remote agent queries",
    )
    verify.add_argument(
        "--partner",
        action="store_true",
        help="run Apigee partner front-door conformance probes from the test VM (tests/partner/probe.py)",
    )
    verify.add_argument(
        "--model-armor",
        action="store_true",
        help="run Model Armor checks: injection on model calls, gateway tools/call inspection, long-call regression",
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

    catalog_p = sub.add_parser(
        "catalog",
        help="resolve, compile and inspect the catalog (catalog/: teams, callers, targets, policy)",
    )
    catalog_sub = catalog_p.add_subparsers(dest="catalog_cmd", required=True)
    resolve_p = catalog_sub.add_parser(
        "resolve",
        help="merge catalog sources + environment overrides, validate, and print or write the result",
    )
    resolve_p.add_argument("--env", default="dev", help="catalog environment, or 'all' with --write/--check")
    resolve_p.add_argument(
        "--format",
        choices=["json", "hcl", "terraform"],
        default="json",
        help="json: full resolved catalog; hcl: the Terraform catalog map as HCL; terraform: the "
        "catalog/.resolved/<env>.json content (default: json)",
    )
    resolve_mode = resolve_p.add_mutually_exclusive_group()
    resolve_mode.add_argument("--write", action="store_true", help="write catalog/.resolved/<env>.json")
    resolve_mode.add_argument("--check", action="store_true", help="exit 1 if catalog/.resolved/<env>.json is stale")
    resolve_p.add_argument("--repo", help="repository root (default: $ESMERALDA_REPO or git checkout root)")
    show_p = catalog_sub.add_parser("show", help="who can call each target, through which gateway, and who pays")
    show_p.add_argument("--target", help="only this target")
    show_p.add_argument("--caller", help="only what this caller can call")
    show_p.add_argument("--provenance", action="store_true",
                        help="also show where each entry and external field came from")
    show_p.add_argument("--inventory", action="store_true",
                        help="show the discovered inventory (external entries no catalog file adopts; never compiled)")
    _add_common_ops_flags(show_p, with_env=True)
    sources_p = catalog_sub.add_parser(
        "sources", help="list catalog sources: type, enabled, capabilities, snapshot path, sha256 and entry count"
    )
    _add_common_ops_flags(sources_p, with_env=True)
    fetch_p = catalog_sub.add_parser(
        "fetch",
        help="read live catalog sources (network, read-only) into normalized snapshots under catalog/snapshots/",
    )
    fetch_p.add_argument("--source", required=True, help="source name in catalog/sources.yaml, or 'all' (enabled ones)")
    fetch_p.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    _add_common_ops_flags(fetch_p, with_env=True)
    diff_p = catalog_sub.add_parser(
        "diff",
        help="drift findings: discovered entries, shadowed or overridden external values, missing snapshots "
        "(exit 3 when there are findings)",
    )
    diff_p.add_argument("--live", action="store_true", help="also compare each live source with its snapshot (network)")
    _add_common_ops_flags(diff_p, with_env=True)

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
    if args.cmd == "catalog":
        return _catalog(args)
    return _ops_dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
