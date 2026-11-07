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
"""End-to-end verification of a deployed environment, reduced to one summary table.

Steps (all run, even after a failure, so one call shows the whole picture):
  1. gateway       make test-gateway ENV=<env>   (every route through the entry point)
  2. specialist    make query AGENT=ai-coe-mortgage-specialist TARGET=remote ENV=<env>
  3. orchestrator  make query AGENT=cx-mortgage-orchestrator TARGET=remote ENV=<env>
                   (a real answer is required: `esmeralda query` fails on an empty answer)

Each step's full output goes to .plans/<env>/_verify/<step>.log; only the agents' answers, the
tail of failed steps and the table are printed. Exit 0 when every step passes, 1 otherwise.

Usage: scripts/verify_env.py <env> [--verbose]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass
class Step:
    name: str
    cmd: list[str]
    show_answer: bool = False


@dataclass
class Result:
    name: str
    ok: bool
    seconds: float
    log: Path
    detail: str = ""


def steps_for(env: str) -> list[Step]:
    make = ["make", "--no-print-directory"]
    return [
        Step("gateway", [*make, "test-gateway", f"ENV={env}"]),
        Step(
            "specialist",
            [*make, "query", "AGENT=ai-coe-mortgage-specialist", "TARGET=remote", f"ENV={env}"],
            show_answer=True,
        ),
        Step(
            "orchestrator",
            [*make, "query", "AGENT=cx-mortgage-orchestrator", "TARGET=remote", f"ENV={env}"],
            show_answer=True,
        ),
    ]


def extract_answer(text: str, max_lines: int = 6) -> str:
    """The `🤖 Answer` section printed by `esmeralda query` (first lines only), "" when none."""
    lines = _ANSI.sub("", text).splitlines()
    for i, line in enumerate(lines):
        if line.startswith("🤖 Answer"):
            body = []
            for row in lines[i + 1 :]:
                if row.startswith("📋 Result"):
                    break
                if row.strip() and row.strip() != "(no text answer)":
                    body.append(row.strip()[:200])
            more = " …" if len(body) > max_lines else ""
            return "\n".join(body[:max_lines]) + more
    return ""


def summary_table(results: list[Result]) -> str:
    width = max(len(r.name) for r in results)
    lines = [f"{'STEP':<{width}}  RESULT  TIME", f"{'-' * width}  ------  ------"]
    for r in results:
        lines.append(
            f"{r.name:<{width}}  {'PASS' if r.ok else 'FAIL':<6}  {r.seconds:5.0f}s"
            + (f"  {r.detail}" if r.detail else "")
        )
    return "\n".join(lines)


def run_step(step: Step, log_dir: Path, verbose: bool) -> Result:
    log = log_dir / f"{step.name}.log"
    t0 = time.monotonic()
    proc = subprocess.run(step.cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    seconds = time.monotonic() - t0
    output = proc.stdout + proc.stderr
    log.write_text(output)
    ok = proc.returncode == 0
    clean = _ANSI.sub("", output)
    if verbose:
        print(clean)
    answer = extract_answer(output) if step.show_answer else ""
    if step.show_answer and ok and not answer:
        ok = False  # the CLI already fails on an empty answer; this guards against output changes
    mark = "✅" if ok else "❌"
    print(f"{mark} {step.name} ({seconds:.0f}s)  log: {log.relative_to(REPO_ROOT)}")
    if answer:
        print("\n".join("   │ " + ln for ln in answer.splitlines()))
    if not ok and not verbose:
        tail = "\n".join(clean.splitlines()[-15:])
        print("\n".join("   ! " + ln for ln in tail.splitlines()))
    detail = "" if ok else f"exit {proc.returncode}" if proc.returncode else "no answer"
    return Result(step.name, ok, seconds, log, detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("env")
    parser.add_argument("--verbose", action="store_true", help="print each step's full output")
    args = parser.parse_args(argv)
    log_dir = REPO_ROOT / ".plans" / args.env / "_verify"
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"🔎 Verifying {args.env}: gateway routes, specialist query, orchestrator query")
    results = [run_step(step, log_dir, args.verbose) for step in steps_for(args.env)]
    print()
    print(summary_table(results))
    failed = [r.name for r in results if not r.ok]
    print(f"\n{'❌ FAILED: ' + ', '.join(failed) if failed else '✅ ' + args.env + ' verified end to end'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
