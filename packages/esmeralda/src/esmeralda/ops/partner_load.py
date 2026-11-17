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

"""Locust load test THROUGH the Apigee partner front door, run from the test VM (``make load-test-partner``).

The partner host (``partner.<domain>``) is private, so Locust runs on the test VM:

1. resolves the VM, entry point, internal CA and partner key like ``esmeralda verify --partner``
   (``esmeralda.ops.testvm``);
2. sends ``tests/load_test/partner_load_test.py`` and ``tests/partner/scenarios.py`` to the VM with
   ``scripts/vm/partner_load.sh``, which installs Locust there on first use (cached venv, with uv)
   and runs it headless;
3. copies the CSV + HTML reports back to ``tests/load_test/.results/partner/`` and prints a
   per-scenario summary.

Usage::

    uv run --package esmeralda python -m esmeralda.ops.partner_load [env] [--users N] [--spawn-rate R]
        [--duration 5m] [--stop-timeout 90] [--include-quota]

``--include-quota`` adds the destructive quota-exhaustion scenario: it burns the API product's LLM
token quota, after which every orchestrator call gets HTTP 429 until the quota window resets.
"""

from __future__ import annotations

import argparse
import csv
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

from esmeralda.ops import repo, testvm
from esmeralda.ops.repo import OpsError

VM_SCRIPT = Path("scripts/vm/partner_load.sh")
FILES = {"SCENARIOS": Path("tests/partner/scenarios.py"), "LOCUSTFILE": Path("tests/load_test/partner_load_test.py")}
RESULTS_DIR = Path("tests/load_test/.results/partner")
DEFAULT_LOCUST_VERSION = "2.46.7"
UV_VERSION = "0.9.30"
_DURATION = re.compile(r"^[0-9]+[smh]?$")
_LOCUST_EXIT = re.compile(r"^LOCUST_EXIT=([0-9]+)", re.MULTILINE)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="partner_load", description=__doc__.split("\n\n")[0])
    parser.add_argument("env", nargs="?", default="dev")
    parser.add_argument("--users", type=int, default=2)
    parser.add_argument("--spawn-rate", type=float, default=1)
    parser.add_argument("--duration", default="5m", help="90s, 5m or 1h")
    parser.add_argument("--stop-timeout", type=int, default=90)
    parser.add_argument("--include-quota", action="store_true", help="add the destructive quota exhaustion")
    args = parser.parse_args(argv)
    if args.users < 0 or args.spawn_rate < 0 or args.stop_timeout < 0:
        parser.error("--users, --spawn-rate and --stop-timeout must be non-negative numbers")
    if not _DURATION.match(args.duration):
        parser.error("--duration must look like 90s, 5m or 1h")
    return args


def seconds(duration: str) -> int:
    """``90s`` / ``5m`` / ``1h`` (or plain seconds) in seconds."""
    unit = duration[-1] if duration[-1] in "smh" else "s"
    return int(duration.rstrip("smh")) * {"s": 1, "m": 60, "h": 3600}[unit]


def locust_version(root: Path) -> str:
    """The Locust version pinned in ``uv.lock`` (so the VM and ``make load-test`` agree)."""
    try:
        match = re.search(r'^name = "locust"\nversion = "([^"]+)"', (root / "uv.lock").read_text(), re.MULTILINE)
    except OSError:
        match = None
    return match.group(1) if match else DEFAULT_LOCUST_VERSION


def spawn_rate(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def summary_lines(results: Path, shown: Path = RESULTS_DIR) -> list[str]:
    """Per-scenario lines from ``partner_stats.csv`` (+ the failures from ``partner_failures.csv``)."""
    lines = [f"📊 Per-scenario results (ms) — {shown / 'partner_stats.csv'}"]
    with (results / "partner_stats.csv").open(newline="") as handle:
        for row in list(csv.reader(handle))[1:]:
            row = row + [""] * (17 - len(row))
            lines.append(
                f"  {row[1]:<48} {row[0]:<6} {row[2]:>6} req {row[3]:>5} fail  p50 {row[11]:>7}  p95 {row[16]:>7}"
            )
    failures = results / "partner_failures.csv"
    if failures.is_file():
        rows = failures.read_text().splitlines()
        if len(rows) > 1:
            lines.append(f"❌ Failures — {shown / 'partner_failures.csv'}")
            lines.extend(f"  {row}" for row in rows[1:])
    return lines


def scp_from(vm: testvm.TestVM, remote: str, local: Path, *, timeout: float = 300.0) -> bool:
    cmd = [
        "gcloud",
        "compute",
        "scp",
        "--recurse",
        f"--zone={vm.zone}",
        f"--project={vm.project}",
        "--tunnel-through-iap",
        "--quiet",
        f"{vm.name}:{remote}",
        f"{local}/",
    ]
    try:
        return subprocess.run(cmd, env=repo.tool_env(), timeout=timeout).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def cleanup(vm: testvm.TestVM, remote_dir: str) -> None:
    try:
        subprocess.run(
            testvm.ssh_cmd(vm, f"rm -rf ~/{remote_dir}"), env=repo.tool_env(), capture_output=True, timeout=120
        )
    except subprocess.TimeoutExpired:
        pass


def run(root: Path, args: argparse.Namespace) -> int:
    script = root / VM_SCRIPT
    if not script.is_file():
        raise OpsError(f"{VM_SCRIPT} not found in this checkout")
    if not testvm.apigee_env(root, args.env):
        raise OpsError(f"Apigee is not deployed in {args.env}: nothing to load test.")
    vm = testvm.resolve(root, args.env)
    key = testvm.partner_key(root, args.env)
    version = locust_version(root)
    run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(3)}"
    remote_dir = f"esmeralda-partner-load/{run_id}"
    host = f"partner.{vm.domain}"
    print(
        f"⚡ Partner load test {run_id}: {args.users} user(s), {args.duration}, "
        f"quota exhaustion {'ON' if args.include_quota else 'off'}"
    )
    print(f"   https://{host} from {vm.name} (Locust {version})", flush=True)
    variables = {
        "PARTNER_ENTRY_IP": vm.entry_ip,
        "PARTNER_HOST": host,
        "PARTNER_API_KEY": key,
        "CA_PEM": vm.ca_pem,
        "PARTNER_INCLUDE_QUOTA": "1" if args.include_quota else "0",
        "PARTNER_LOAD_RUN_ID": run_id,
        "REMOTE_DIR": remote_dir,
        "LOCUST_VERSION": version,
        "UV_VERSION": UV_VERSION,
        "USERS": str(args.users),
        "SPAWN_RATE": spawn_rate(args.spawn_rate),
        "DURATION": args.duration,
        "STOP_TIMEOUT": str(args.stop_timeout),
    }
    files = {name: root / path for name, path in FILES.items()}
    timeout = seconds(args.duration) + args.stop_timeout + 900  # + Locust install and SSH set-up
    result = testvm.run(vm, script.read_text(), vars=variables, files=files, timeout=timeout, echo=True)
    exits = _LOCUST_EXIT.findall(result.stdout)
    if not exits:
        raise OpsError(f"The remote run did not finish (ssh exit {result.returncode}).")
    locust_rc = int(exits[-1])

    results = root / RESULTS_DIR
    results.mkdir(parents=True, exist_ok=True)
    for old in [*results.glob("partner_*.csv"), results / "report.html"]:
        old.unlink(missing_ok=True)
    if not scp_from(vm, f"{remote_dir}/results/*", results):
        raise OpsError(
            f"Could not copy the reports back (gcloud auth expired?); they stay on {vm.name} in ~/{remote_dir}/results."
        )
    cleanup(vm, remote_dir)

    print()
    print("\n".join(summary_lines(results)))
    print(f"📄 HTML report: {RESULTS_DIR / 'report.html'}")
    print(
        f"   Run {run_id}: requests carry the header x-esmeralda-load-run={run_id} "
        "(visible in an Apigee Debug session)."
    )
    return locust_rc


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(repo.repo_root(), args)
    except OpsError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
