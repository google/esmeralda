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

* ``esmeralda run -- <command> [args...]``: the container entrypoint. Installs the Agent Gateway
  root CA, then replaces itself with the server command (``exec``), so the server keeps PID 1 and
  receives signals directly.
* ``esmeralda serve [options]``: serves an agent (ADK API server or A2A server) the same way locally
  and in the container. ``--local`` applies ``local_env`` from ``agent.yaml``.
* ``esmeralda query [options] MESSAGE``: queries an agent (ADK or A2A) in-process, on a running
  server (``--url``) or on Agent Runtime (``--engine``), and exits non-zero if the run failed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from esmeralda import certs

USAGE = "usage: esmeralda {run,serve,query} ...  (esmeralda run -- <command> [args...])"


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="esmeralda", description="Esmeralda agent runtime tools")
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
    return _query(args)


if __name__ == "__main__":
    sys.exit(main())
