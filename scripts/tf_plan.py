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
"""Plan / apply one Terragrunt unit non-interactively, with a compact, token-efficient summary.

  plan      terragrunt plan -out=<file> -lock=false, then `show -json`; prints one line per
            changed resource and saves the full text + JSON under .plans/<env>/<unit>/.
  apply     applies ONLY that saved plan. Refuses a missing plan, and refuses deletes/replaces
            unless --allow-destroy.
  summarize prints the summary of an existing plan JSON (no Terraform call).

Exit codes (plan, summarize): 0 no changes, 2 changes, 1 error. apply: 0 applied, 1 refused/error.
Standard library only, so it runs anywhere with `uv run --no-project python scripts/tf_plan.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXIT_NO_CHANGES, EXIT_ERROR, EXIT_CHANGES = 0, 1, 2

REPO_ROOT = Path(__file__).resolve().parent.parent
PLANS_DIR = REPO_ROOT / ".plans"
LIVE_DIR = REPO_ROOT / "infrastructure" / "live"

# Terraform action lists (resource_changes[].change.actions) -> one action name.
_SYMBOL = {"create": "+", "update": "~", "delete": "-", "replace": "-/+", "read": "<=", "forget": "!", "no-op": " "}
_DESTRUCTIVE = ("delete", "replace")

# Quiet, non-interactive Terragrunt/Terraform for every call made by this script.
QUIET_ENV = {
    "TF_IN_AUTOMATION": "1",
    "TF_INPUT": "0",
    "TG_NON_INTERACTIVE": "true",
    "TG_LOG_LEVEL": os.environ.get("TG_LOG_LEVEL", "warn"),
    "TG_NO_COLOR": "true",
    "TG_TF_FORWARD_STDOUT": "true",
}


def action_of(actions: list[str]) -> str:
    """Maps a Terraform action list to create/update/delete/replace/read/forget/no-op."""
    acts = list(actions or [])
    if "delete" in acts and "create" in acts:
        return "replace"
    if len(acts) == 1 and acts[0] in _SYMBOL:
        return acts[0]
    return "+".join(acts) or "no-op"


def _has_true(value: Any) -> bool:
    """after_unknown / *_sensitive mirror the value's shape with `true` leaves where it applies."""
    if isinstance(value, dict):
        return any(_has_true(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_true(v) for v in value)
    return value is True


def changed_attributes(change: dict[str, Any]) -> list[str]:
    """Top-level attribute names that differ between before and after (values are never printed)."""
    before = change.get("before") or {}
    after = change.get("after") or {}
    unknown = change.get("after_unknown") or {}
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    keys = set(before) | set(after)
    changed = {k for k in keys if before.get(k) != after.get(k)}
    if isinstance(unknown, dict):
        changed |= {k for k, v in unknown.items() if _has_true(v)}
    # A value that only becomes (or stops being) sensitive is a change too.
    bs, as_ = change.get("before_sensitive") or {}, change.get("after_sensitive") or {}
    if isinstance(bs, dict) and isinstance(as_, dict):
        changed |= {k for k in set(bs) | set(as_) if _has_true(bs.get(k)) != _has_true(as_.get(k))}
    return sorted(changed)


def replace_reasons(change: dict[str, Any]) -> list[str]:
    """Top-level attributes that force a replacement (from replace_paths)."""
    names = []
    for path in change.get("replace_paths") or []:
        if path and isinstance(path[0], str) and path[0] not in names:
            names.append(path[0])
    return names


@dataclass
class ResourceLine:
    address: str
    action: str
    attrs: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class PlanSummary:
    resources: list[ResourceLine]
    outputs: list[tuple[str, str]]
    drift: int = 0
    errored: bool = False

    def counts(self) -> dict[str, int]:
        counts = {a: 0 for a in ("create", "update", "replace", "delete", "read")}
        for r in self.resources:
            counts[r.action] = counts.get(r.action, 0) + 1
        return counts

    @property
    def has_changes(self) -> bool:
        return bool(self.resources or self.outputs)

    @property
    def destructive(self) -> list[ResourceLine]:
        return [r for r in self.resources if r.action in _DESTRUCTIVE]


def summarize(plan: dict[str, Any]) -> PlanSummary:
    """Reduces `terraform show -json <planfile>` to the lines worth reading."""
    resources = []
    for rc in plan.get("resource_changes") or []:
        change = rc.get("change") or {}
        action = action_of(change.get("actions") or [])
        notes = []
        if rc.get("previous_address") and rc["previous_address"] != rc.get("address"):
            notes.append(f"moved from {rc['previous_address']}")
        if change.get("importing"):
            notes.append("import")
        if action == "no-op":
            if notes:  # a pure move/import is still worth a line
                resources.append(ResourceLine(rc.get("address", "?"), "move", [], ", ".join(notes)))
            continue
        attrs: list[str] = []
        if action == "update":
            attrs = changed_attributes(change)
        elif action == "replace":
            attrs = replace_reasons(change) or changed_attributes(change)
        reason = rc.get("action_reason")
        if reason and action in ("replace", "delete", "read"):
            notes.append(reason.lower().replace("_", " "))
        resources.append(ResourceLine(rc.get("address", "?"), action, attrs, ", ".join(notes)))

    outputs = []
    for name, oc in sorted((plan.get("output_changes") or {}).items()):
        action = action_of(oc.get("actions") or [])
        if action != "no-op":
            outputs.append((name, action))

    return PlanSummary(
        resources=resources,
        outputs=outputs,
        drift=len(plan.get("resource_drift") or []),
        errored=bool(plan.get("errored")),
    )


def render(summary: PlanSummary, label: str) -> str:
    """Compact text: header with counts, then one line per resource and output change."""
    c = summary.counts()
    lines = []
    if not summary.has_changes:
        lines.append(f"📋 {label}: No changes.")
    else:
        head = ", ".join(f"{c[a]} {a}" for a in ("create", "update", "replace", "delete", "read") if c.get(a))
        moves = c.get("move", 0)
        head = head + (f", {moves} move" if moves else "")
        lines.append(
            f"📋 {label}: {head or 'outputs only'}" + (f", {len(summary.outputs)} output(s)" if summary.outputs else "")
        )
    order = {"replace": 0, "delete": 1, "update": 2, "create": 3, "read": 4}
    width = max((len(r.address) for r in summary.resources), default=0)
    for r in sorted(summary.resources, key=lambda r: (order.get(r.action, 5), r.address)):
        extra = f" [{', '.join(r.attrs)}]" if r.attrs else ""
        note = f" ({r.note})" if r.note else ""
        lines.append(f"  {_SYMBOL.get(r.action, '?'):>3} {r.address:<{width}}  {r.action}{extra}{note}")
    if summary.outputs:
        lines.append("  outputs: " + ", ".join(f"{_SYMBOL.get(a, '?')}{n}" for n, a in summary.outputs))
    if summary.drift:
        lines.append(f"  ℹ️  {summary.drift} resource(s) drifted outside Terraform (refreshed into the plan)")
    if summary.errored:
        lines.append("  ❌ the plan errored (partial plan)")
    return "\n".join(lines)


def apply_guard(summary: PlanSummary | None, *, allow_destroy: bool) -> str | None:
    """Returns why a saved plan must not be applied, or None when it may."""
    if summary is None:
        return "no saved plan: run `make plan UNIT=... ENV=...` first"
    if summary.errored:
        return "the saved plan errored; re-run make plan"
    if summary.destructive and not allow_destroy:
        names = ", ".join(f"{r.address} ({r.action})" for r in summary.destructive)
        return f"the plan deletes/replaces {len(summary.destructive)} resource(s): {names}. Re-run with ALLOW_DESTROY=1"
    return None


# --------------------------------------------------------------------------------------------------
# Terragrunt plumbing
# --------------------------------------------------------------------------------------------------


def unit_paths(env: str, unit: str) -> tuple[Path, Path]:
    unit = unit.strip("/")
    unit_dir = LIVE_DIR / env / unit
    if not (unit_dir / "terragrunt.hcl").is_file():
        raise SystemExit(f"❌ {unit_dir.relative_to(REPO_ROOT)} is not a Terragrunt unit (no terragrunt.hcl)")
    return unit_dir, PLANS_DIR / env / unit


def _run(cmd: list[str], cwd: Path, log: Path) -> int:
    env = {**os.environ, **QUIET_ENV}
    with log.open("w") as out:
        return subprocess.run(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, check=False).returncode


_LOG_PREFIX = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d+\s+[A-Z]+\s+")


def _tail(path: Path, n: int = 25) -> str:
    """Last lines of a log, without Terragrunt timestamps and repeated lines (errors cascade)."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    seen: set[str] = set()
    unique = []
    for line in (_LOG_PREFIX.sub("", ln) for ln in lines):
        if line.strip() and line not in seen:
            seen.add(line)
            unique.append(line)
    return "\n".join(unique[-n:])


def cmd_plan(args: argparse.Namespace) -> int:
    unit_dir, out = unit_paths(args.env, args.unit)
    out.mkdir(parents=True, exist_ok=True)
    planfile, text, js, summary_file = out / "plan.tfplan", out / "plan.txt", out / "plan.json", out / "summary.txt"
    for f in (planfile, js, summary_file):
        f.unlink(missing_ok=True)
    label = f"{args.env}/{args.unit.strip('/')}"
    t0 = time.monotonic()

    rc = _run(["terragrunt", "plan", f"-out={planfile}", "-lock=false", "-no-color", "-input=false"], unit_dir, text)
    if rc != 0 or not planfile.exists():
        print(f"❌ terragrunt plan failed for {label} (exit {rc}). Last lines of {text.relative_to(REPO_ROOT)}:")
        print(_tail(text))
        return EXIT_ERROR
    show_log = out / "show.log"
    rc = _run(["terragrunt", "show", "-json", "-no-color", str(planfile)], unit_dir, show_log)
    raw = show_log.read_text(errors="replace")
    plan = _extract_json(raw)
    if rc != 0 or plan is None:
        print(f"❌ terragrunt show -json failed for {label} (exit {rc}):\n{_tail(show_log)}")
        return EXIT_ERROR
    js.write_text(json.dumps(plan))  # holds state values (secrets too): stays local, like the state
    show_log.unlink(missing_ok=True)

    summary = summarize(plan)
    report = render(summary, label)
    summary_file.write_text(report + "\n")
    if args.full:
        print(plan_body(text.read_text(errors="replace")))
    print(report)
    print(f"  saved: {out.relative_to(REPO_ROOT)}/{{plan.txt,plan.json,plan.tfplan}} ({time.monotonic() - t0:.0f}s)")
    if summary.errored:
        return EXIT_ERROR
    return EXIT_CHANGES if summary.has_changes else EXIT_NO_CHANGES


_REFRESH = re.compile(r": (Refreshing state\.\.\.|Reading\.\.\.|Read complete after )")


def plan_body(text: str) -> str:
    """The plan text without the init block and the per-resource refresh lines."""
    marker = "Terraform has been successfully initialized!"
    if marker in text:
        text = text.split(marker, 1)[1]
    return "\n".join(ln for ln in text.splitlines() if not _REFRESH.search(ln)).strip("\n")


def _extract_json(raw: str) -> dict[str, Any] | None:
    """`show -json` prints one JSON document; tolerate log lines around it."""
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def load_saved(env: str, unit: str) -> tuple[PlanSummary | None, Path]:
    _, out = unit_paths(env, unit)
    planfile, js = out / "plan.tfplan", out / "plan.json"
    if not planfile.exists() or not js.exists():
        return None, out
    return summarize(json.loads(js.read_text())), out


def cmd_apply(args: argparse.Namespace) -> int:
    unit_dir, _ = unit_paths(args.env, args.unit)
    label = f"{args.env}/{args.unit.strip('/')}"
    summary, out = load_saved(args.env, args.unit)
    if summary is not None:
        print(render(summary, label))
        age = time.time() - (out / "plan.tfplan").stat().st_mtime
        if age > 3600:
            print(f"  ⚠️  the saved plan is {age / 3600:.1f}h old; Terraform refuses it if the state changed since")
    refusal = apply_guard(summary, allow_destroy=args.allow_destroy)
    if refusal:
        print(f"⛔ Refusing to apply {label}: {refusal}")
        return EXIT_ERROR

    planfile = out / "plan.tfplan"
    log = out / "apply.txt"
    t0 = time.monotonic()
    rc = _run(["terragrunt", "apply", "-no-color", "-input=false", str(planfile)], unit_dir, log)
    if rc != 0:
        print(f"❌ terragrunt apply failed for {label} (exit {rc}). Last lines of {log.relative_to(REPO_ROOT)}:")
        print(_tail(log))
        return EXIT_ERROR
    planfile.unlink(missing_ok=True)  # a saved plan is single-use
    tail = [ln for ln in log.read_text(errors="replace").splitlines() if ln.startswith("Apply complete")]
    result = tail[-1] if tail else "applied"
    print(f"✅ {label}: {result} ({time.monotonic() - t0:.0f}s, log {log.relative_to(REPO_ROOT)})")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan_json).read_text())
    summary = summarize(plan)
    print(render(summary, args.label or args.plan_json))
    if summary.errored:
        return EXIT_ERROR
    return EXIT_CHANGES if summary.has_changes else EXIT_NO_CHANGES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="plan a unit and print the compact summary")
    p.add_argument("--env", required=True)
    p.add_argument("--unit", required=True, help="path under infrastructure/live/<env>/")
    p.add_argument("--full", action="store_true", help="also print the full plan text")
    a = sub.add_parser("apply", help="apply the saved plan of a unit")
    a.add_argument("--env", required=True)
    a.add_argument("--unit", required=True)
    a.add_argument("--allow-destroy", action="store_true", help="allow deletes and replaces")
    s = sub.add_parser("summarize", help="summarize a `terraform show -json` plan file")
    s.add_argument("plan_json")
    s.add_argument("--label", default="")
    args = parser.parse_args(argv)
    try:
        return {"plan": cmd_plan, "apply": cmd_apply, "summarize": cmd_summarize}[args.cmd](args)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return EXIT_ERROR
        raise
    except (OSError, json.JSONDecodeError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
